"""Gemini scene triage: when the camera finds a person, ask Gemini if they need help.

On a new person sighting (and again every `recheck_s` while they stay in view,
or when the operator presses "Ask Gemini"), this grabs the AI Camera's current
annotated frame, sends it to the Gemini API with the detector's numbered boxes,
and gets back a structured assessment: how many *unique* real people are in view
(duplicate boxes, reflections, and pictures of people don't count), does anyone
appear to need help, how urgent it is, posture, visible hazards, what the rescue
team should do, and what the robot should say. With --voice, that sentence is
what the robot speaks (ElevenLabs), instead of the fixed "Person detected" alerts.

It runs on its own thread and only reads the camera status and the camera's
snapshot URL. It never touches control, arming, motors, or accessories, so a
slow or failed Gemini call can't delay Stop. At most one request is in flight,
each has a timeout, and every result (image + JSON) is saved for later review.

Needs the GEMINI_API_KEY environment variable (GOOGLE_API_KEY also works) and
internet access. Uses only the standard library (Gemini REST API).

Test on its own, with the dashboard's live camera running:
    python3 -m rescuebot.gemini                      # assess the current frame
    python3 -m rescuebot.gemini path/to/photo.jpg    # assess a saved image
"""

from __future__ import annotations

import base64
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from .voice import CAMERA_HFOV_DEG, alert_text, with_bearings

# Flash-Lite answers in about 1-2 s, fast enough for the robot to talk; the
# second model is tried when the first is overloaded or rate-limited.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODELS = ("gemini-3.1-flash-lite",)
RETRY_STATUS = {429, 500, 502, 503, 504}
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:8081/snapshot.jpg"
DEFAULT_SAVE_DIR = Path.home() / "rescuebot_runs" / "gemini"
URGENCY = ("none", "low", "medium", "high")
POSTURES = ("standing", "sitting", "lying", "unclear", "none")
MATCHES = ("new", "known", "unclear")
MAX_ROSTER = 30  # people remembered per session; keeps the prompt short

PROMPT = """You are the triage assistant on Rescuebot, a small search-and-rescue robot \
that scouts dangerous buildings (fire, smoke, possible collapse) ahead of human rescuers. \
The image is the robot's front camera view, about 30 cm above the floor. Boxes and labels \
on it were drawn by the robot's on-board person detector.

{context}

Assess only what is actually visible:
- unique_people: how many distinct, real people are visible. Count each person once. The \
detector sometimes draws two boxes on one person, or boxes a reflection, a poster, a photo, \
a screen, or a mannequin; none of those add a person.
- people_note: if unique_people differs from the number of detector boxes, say briefly why \
(for example "boxes 1 and 2 are the same person" or "box 3 is a poster"). Otherwise empty.
- needs_help: true if anyone appears to need help (lying on the floor, slumped, injured, \
trapped, waving or signaling, near fire or smoke).
- urgency: none (no one visible), low (person looks fine and mobile), medium (may need \
help), high (appears injured, unresponsive, trapped, or in immediate danger).
- posture of the most important person: standing, sitting, lying, unclear, or none.
- hazards: visible dangers such as fire, smoke, water, debris, exposed wires, or sharp objects. \
Empty if none.
- people: one entry per unique person counted above, left to right. For each:
  - appearance: what tells this person apart, in at most 12 words (clothing colors, hair, \
build, anything distinctive). Never guess identity, age, or ethnicity.
  - where: left, ahead, or right.
  - match: "known" if they are clearly one of the people already seen (give that number in \
known_id), "new" if they clearly are not, or "unclear" if too little is visible to tell (for \
example only legs, far away, or too dark). When in doubt between known and new, use unclear.
  - known_id: the matching person's number when match is "known", otherwise 0.
- summary: one or two plain sentences for the rescue team.
- recommended_action: one short instruction for the rescue team.
- spoken_alert: what you, speaking as the robot, say out loud to the rescue team through \
its speaker. First person, calm and clear, at most 25 words. Say how many unique people you \
see, where (left, right, ahead), their condition, and whether they need help. \
Example: "I see two people. The person on the left is lying down and not moving. Send help now."

Never invent details. If the image is dark or blurry, say so and use "unclear"."""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "unique_people": {"type": "INTEGER"},
        "people_note": {"type": "STRING"},
        "needs_help": {"type": "BOOLEAN"},
        "urgency": {"type": "STRING", "enum": list(URGENCY)},
        "posture": {"type": "STRING", "enum": list(POSTURES)},
        "hazards": {"type": "ARRAY", "items": {"type": "STRING"}},
        "summary": {"type": "STRING"},
        "recommended_action": {"type": "STRING"},
        "spoken_alert": {"type": "STRING"},
        "people": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "appearance": {"type": "STRING"},
                    "where": {"type": "STRING", "enum": ["left", "ahead", "right"]},
                    "match": {"type": "STRING", "enum": list(MATCHES)},
                    "known_id": {"type": "INTEGER"},
                },
                "required": ["appearance", "where", "match", "known_id"],
            },
        },
    },
    "required": ["unique_people", "people_note", "needs_help", "urgency", "posture", "hazards",
                 "summary", "recommended_action", "spoken_alert", "people"],
}


class GeminiError(RuntimeError):
    """A Gemini call failed; the message is safe to show on the dashboard."""


def api_key_from_env() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if value is not None else ""


def normalize(raw: Any) -> dict[str, Any]:
    """Validate Gemini's JSON and clamp it to what the dashboard expects."""
    if not isinstance(raw, dict):
        raise GeminiError("Gemini returned something other than a JSON object")
    try:
        people = max(0, min(int(raw.get("unique_people", 0)), 50))
    except (TypeError, ValueError):
        people = 0
    urgency = raw.get("urgency") if raw.get("urgency") in URGENCY else "low"
    posture = raw.get("posture") if raw.get("posture") in POSTURES else "unclear"
    hazards = raw.get("hazards") if isinstance(raw.get("hazards"), list) else []
    summary = _text(raw.get("summary"), 400)
    if not summary:
        raise GeminiError("Gemini returned no summary")
    seen = []
    for item in raw.get("people") if isinstance(raw.get("people"), list) else []:
        if not isinstance(item, dict):
            continue
        match = item.get("match") if item.get("match") in MATCHES else "unclear"
        try:
            known_id = int(item.get("known_id") or 0)
        except (TypeError, ValueError):
            known_id = 0
        if match == "known" and known_id <= 0:
            match = "unclear"
        seen.append({
            "appearance": _text(item.get("appearance"), 100),
            "where": item.get("where") if item.get("where") in ("left", "ahead", "right") else "ahead",
            "match": match,
            "known_id": known_id if match == "known" else 0,
        })
        if len(seen) >= 12:
            break
    return {
        "unique_people": people,
        "people_note": _text(raw.get("people_note"), 160),
        "needs_help": raw.get("needs_help") is True,
        "urgency": urgency,
        "posture": posture,
        "hazards": [h for h in (_text(h, 60) for h in hazards[:6]) if h],
        "summary": summary,
        "recommended_action": _text(raw.get("recommended_action"), 200),
        "spoken_alert": _text(raw.get("spoken_alert"), 200),
        "people": seen,
    }


def roster_text(roster: list[dict[str, Any]]) -> str:
    """The people already seen this session, so Gemini can tell new from known."""
    if not roster:
        return "No one has been seen yet this session, so every clearly visible person is new."
    lines = [f"#{p['id']}: {p['appearance'] or 'no description'} (last seen {p['last_where']})"
             for p in roster[-MAX_ROSTER:]]
    return ("People already seen this session (the robot may have moved since, so position is "
            "only a hint; match by appearance):\n" + "\n".join(lines))


def context_text(detections: list[dict[str, Any]], hfov_deg: float = CAMERA_HFOV_DEG) -> str:
    """Describe the detector's results in words, so Gemini knows what to focus on."""
    people = [d for d in with_bearings(detections, hfov_deg) if d.get("label") == "person"]
    if not people:
        return "The on-board detector currently sees no person. Describe the scene and any hazards."
    people.sort(key=lambda d: (d.get("bbox") or {}).get("x", 0.0))
    parts = []
    for number, det in enumerate(people[:8], 1):
        bearing = det.get("bearing_deg") or 0.0
        where = "ahead" if abs(bearing) < 10 else ("to the right" if bearing > 0 else "to the left")
        box = det.get("bbox") or {}
        span = ""
        if box:
            left, right = box.get("x", 0.0), box.get("x", 0.0) + box.get("width", 0.0)
            span = f", spanning {left:.0%} to {right:.0%} of the image width"
        parts.append(f"box {number}: {where} ({bearing:+.0f} degrees{span}, "
                     f"confidence {det.get('confidence', 0):.2f})")
    return (f"The on-board detector drew {len(people)} person box(es), numbered left to right: "
            + "; ".join(parts) + ".")


class _Retryable(GeminiError):
    """Busy, rate-limited, or unreachable: worth trying the next model."""


def call_gemini(jpeg: bytes, context: str, *, api_key: str,
                models: str | tuple[str, ...] = (DEFAULT_MODEL, *FALLBACK_MODELS),
                timeout_s: float = 20.0, url: str = API_URL) -> dict[str, Any]:
    """Send one image to Gemini and return the normalized assessment, plus the model used.

    Tries each model in turn while the previous one is overloaded, rate-limited,
    or unreachable. Other errors (bad key, bad request) fail at once.
    """
    models = (models,) if isinstance(models, str) else tuple(models)
    error: GeminiError = GeminiError("No Gemini model configured")
    for model in models:
        try:
            return {**_call_model(jpeg, context, api_key=api_key, model=model,
                                  timeout_s=timeout_s, url=url), "model": model}
        except _Retryable as e:
            error = GeminiError(str(e))
    raise error


def _call_model(jpeg: bytes, context: str, *, api_key: str, model: str,
                timeout_s: float, url: str) -> dict[str, Any]:
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                {"text": PROMPT.format(context=context)},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0.2,
        },
    }
    request = urllib.request.Request(
        url.format(model=model),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        kind = _Retryable if e.code in RETRY_STATUS else GeminiError
        raise kind(f"Gemini API error {e.code} ({model}): {_text(detail, 200) or e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise _Retryable(f"Couldn't reach Gemini ({_text(reason, 120)})") from None
    except ValueError:
        raise GeminiError("Gemini sent a reply that isn't JSON") from None
    try:
        candidate = reply["candidates"][0]
        text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        reason = (reply.get("promptFeedback") or {}).get("blockReason") if isinstance(reply, dict) else None
        raise GeminiError(f"Gemini gave no answer{f' ({reason})' if reason else ''}") from None
    try:
        return normalize(json.loads(text))
    except ValueError:
        raise GeminiError("Gemini's answer wasn't valid JSON") from None


def fetch_snapshot(url: str = DEFAULT_SNAPSHOT_URL, timeout_s: float = 3.0) -> bytes:
    """Get the camera's newest annotated JPEG."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            data = response.read(5_000_000)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GeminiError(f"No camera image ({_text(getattr(e, 'reason', e), 120)})") from None
    if not data.startswith(b"\xff\xd8"):
        raise GeminiError("Camera image isn't a JPEG")
    return data


def _stderr_log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class GeminiTriage:
    """Watches camera detections and asks Gemini about each new person sighting.

    About twice a second it reads `camera_status()`. When a person first appears
    (after `gone_after_s` with no one in view), when the detector draws more
    person boxes than at the last assessment, when someone has stayed in view
    for `recheck_s`, or when the operator asks, it calls `snapshot()` for a JPEG
    and `analyze(jpeg, context)` for the assessment.
    Calls are at least `min_gap_s` apart.

    With a speaker attached, Gemini is the robot's voice: its `spoken_alert` is
    said for every new sighting and operator request; on a recheck or extra
    detector box, only when the unique-people count, urgency, or needs-help
    answer changed. If Gemini
    fails on a new sighting, the fixed detector alert is spoken instead, so the
    robot never goes silent about a person.

    It also keeps a roster of every distinct person seen since it started (or
    since `reset_session()`): each request lists the roster, and Gemini says
    whether each visible person is new, a known person, or unclear. Only "new"
    grows the total, so a partial or dark view never adds a person.
    """

    def __init__(self, camera_status: Callable[[], dict], snapshot: Callable[[], bytes],
                 analyze: Callable[[bytes, str], dict] | None, *, model: str = DEFAULT_MODEL,
                 speaker: Any = None, interval_s: float = 0.5, gone_after_s: float = 3.0,
                 recheck_s: float = 30.0, min_gap_s: float = 8.0,
                 save_dir: Path | None = DEFAULT_SAVE_DIR, missing_reason: str | None = None,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] | None = None):
        self.camera_status = camera_status
        self.snapshot = snapshot
        self.analyze = analyze
        self.model = model
        self.speaker = speaker
        self.interval_s = interval_s
        self.gone_after_s = gone_after_s
        self.recheck_s = recheck_s
        self.min_gap_s = min_gap_s
        self.save_dir = save_dir
        self.clock = clock
        self.log = log or _stderr_log
        self._lock = threading.Lock()
        self._present = False
        self._last_seen = float("-inf")
        self._last_call = float("-inf")
        self._assessed_boxes = 0
        self._manual = False
        self._state = "unavailable" if analyze is None else "watching"
        self._message = missing_reason or "Waiting for a person to appear."
        self._latest: dict[str, Any] | None = None
        self._latest_at: float | None = None
        self._latest_jpeg: bytes | None = None
        self._history: list[dict[str, Any]] = []
        self._calls = 0
        self._roster: list[dict[str, Any]] = []
        self._session_started = datetime.now().isoformat(timespec="seconds")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rescuebot-gemini", daemon=True)

    @property
    def active(self) -> bool:
        """True when Gemini can be called (a key and live video are available)."""
        return self.analyze is not None

    def start(self) -> None:
        if self.analyze is not None:
            self._thread.start()

    def request(self) -> bool:
        """Operator asked for an assessment now. False if Gemini is off or was just called."""
        if self.analyze is None:
            return False
        with self._lock:
            if self._state == "thinking" or self.clock() - self._last_call < self.min_gap_s:
                return False
            self._manual = True
        return True

    def poll_once(self) -> str | None:
        """Check the camera once and assess if needed. Returns the trigger used, or None."""
        now = self.clock()
        status = self.camera_status()
        detections = (status.get("detections") or []) if status.get("status") == "online" else []
        boxes = sum(1 for d in detections if d.get("label") == "person")
        person = boxes > 0
        with self._lock:
            manual, self._manual = self._manual, False
        trigger = None
        if person:
            if not self._present:
                trigger = "new person"
            elif boxes > self._assessed_boxes:
                trigger = "more people"
            elif now - self._last_call >= self.recheck_s:
                trigger = "still in view"
            self._present = True
            self._last_seen = now
        elif self._present and now - self._last_seen >= self.gone_after_s:
            self._present = False
            self._assessed_boxes = 0
        if manual:
            trigger = "operator"
        if trigger is None or (trigger != "operator" and now - self._last_call < self.min_gap_s):
            return None
        self._assess(trigger, detections, now)
        return trigger

    def _assess(self, trigger: str, detections: list[dict[str, Any]], now: float) -> None:
        assert self.analyze is not None
        with self._lock:
            self._state, self._message = "thinking", f"Asking Gemini ({trigger})…"
            self._last_call = now
            # The most boxes already assessed during this sighting, so a box that
            # flickers on and off doesn't trigger a new call each time.
            boxes = sum(1 for d in detections if d.get("label") == "person")
            self._assessed_boxes = max(self._assessed_boxes, boxes)
            self._calls += 1
        started = self.clock()
        with self._lock:
            roster = [dict(p) for p in self._roster]
        try:
            jpeg = self.snapshot()
            result = self.analyze(jpeg, context_text(detections) + "\n\n" + roster_text(roster))
        except GeminiError as e:
            self._fail(str(e), trigger, detections)
            return
        except Exception as e:  # never let a bad reply kill the thread
            self._fail(f"{type(e).__name__}: {_text(e, 160)}", trigger, detections)
            return
        result = {
            "model": self.model,
            **result,
            "trigger": trigger,
            "time": datetime.now().isoformat(timespec="seconds"),
            "latency_ms": round((self.clock() - started) * 1000),
            "detector_people": sum(1 for d in detections if d.get("label") == "person"),
        }
        with self._lock:
            previous = self._latest
            result["new_people"] = self._update_roster(result.get("people") or [], result)
            self._state, self._message = "watching", "Latest assessment is below."
            self._latest, self._latest_jpeg, self._latest_at = result, jpeg, self.clock()
            self._history = [result, *self._history][:5]
        self.log(f"[gemini] {result['unique_people']} unique, {result['urgency']}"
                 f"{', new: ' + ', '.join(f'#{i}' for i in result['new_people']) if result['new_people'] else ''}"
                 f", total {len(self._roster)}: {result['summary']}")
        self._save(result, jpeg)
        if self.speaker is not None and self._worth_saying(trigger, result, previous):
            self.speaker.say(result["spoken_alert"] or result["summary"])

    def _update_roster(self, people: list[dict[str, Any]], result: dict[str, Any]) -> list[int]:
        """Add new people and refresh known ones. Caller holds the lock. Returns new ids."""
        new_ids = []
        known = {p["id"]: p for p in self._roster}
        for person in people:
            if person["match"] == "known" and person["known_id"] in known:
                entry = known[person["known_id"]]
                entry.update(last_seen=result["time"], last_where=person["where"],
                             sightings=entry["sightings"] + 1, urgency=result["urgency"])
                if person["appearance"]:
                    entry["appearance"] = person["appearance"]
            elif person["match"] == "new" and len(self._roster) < 999:
                entry = {"id": len(self._roster) + 1, "appearance": person["appearance"],
                         "first_seen": result["time"], "last_seen": result["time"],
                         "last_where": person["where"], "sightings": 1, "urgency": result["urgency"]}
                self._roster.append(entry)
                known[entry["id"]] = entry
                new_ids.append(entry["id"])
        return new_ids

    def reset_session(self) -> None:
        """Start the total unique-people count again from zero."""
        with self._lock:
            self._roster = []
            self._session_started = datetime.now().isoformat(timespec="seconds")
        self.log("[gemini] total unique people reset to 0")

    @staticmethod
    def _worth_saying(trigger: str, result: dict[str, Any], previous: dict[str, Any] | None) -> bool:
        if trigger in ("new person", "operator") or previous is None:
            return True
        keys = ("unique_people", "urgency", "needs_help")
        return any(result.get(k) != previous.get(k) for k in keys)

    def _fail(self, message: str, trigger: str, detections: list[dict[str, Any]]) -> None:
        with self._lock:
            self._state, self._message = "error", message
        self.log(f"[gemini] {message}")
        if self.speaker is not None and trigger in ("new person", "more people"):
            fallback = alert_text(with_bearings(detections))
            if fallback:
                self.speaker.say(fallback)

    def _save(self, result: dict[str, Any], jpeg: bytes) -> None:
        if self.save_dir is None:
            return
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            stem = self.save_dir / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            stem.with_suffix(".jpg").write_bytes(jpeg)
            stem.with_suffix(".json").write_text(json.dumps(result, indent=2))
        except OSError as e:
            self.log(f"[gemini] couldn't save the assessment ({e})")

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def status(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            can_ask = (self.analyze is not None and self._state != "thinking"
                       and now - self._last_call >= self.min_gap_s)
            latest = None
            if self._latest is not None and self._latest_at is not None:
                latest = {**self._latest, "age_s": round(now - self._latest_at, 1)}
            return {
                "enabled": True,
                "state": self._state,
                "message": self._message,
                "model": self.model,
                "calls": self._calls,
                "can_ask": can_ask,
                "latest": latest,
                "history": [
                    {k: item[k] for k in ("time", "urgency", "needs_help", "unique_people", "summary")}
                    for item in self._history[1:]
                ],
                "session": {
                    "since": self._session_started,
                    "total_unique": len(self._roster),
                    "people": [dict(p) for p in self._roster[-50:]],  # newest 50
                },
            }

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.poll_once()
            except Exception as e:
                self.log(f"[gemini] camera poll failed ({type(e).__name__}: {e})")

    def close(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)


def models_from_text(text: str | None) -> tuple[str, ...]:
    """"a,b" -> ("a", "b"); empty -> the default model plus its fallbacks."""
    models = tuple(m.strip() for m in (text or "").split(",") if m.strip())
    return models or (DEFAULT_MODEL, *FALLBACK_MODELS)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    key = api_key_from_env()
    if not key:
        sys.exit("Set GEMINI_API_KEY first (get one at https://aistudio.google.com/apikey).")
    models = models_from_text(os.environ.get("RESCUEBOT_GEMINI_MODEL"))
    try:
        jpeg = Path(args[0]).read_bytes() if args else fetch_snapshot()
        started = time.monotonic()
        result = call_gemini(jpeg, "No detector results were given; judge from the image.",
                             api_key=key, models=models)
    except (GeminiError, OSError) as e:
        sys.exit(str(e))
    print(json.dumps(result, indent=2))
    print(f"({result['model']}, {time.monotonic() - started:.1f} s)", file=sys.stderr)


if __name__ == "__main__":
    main()
