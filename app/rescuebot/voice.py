"""Spoken alerts for the rescuebot, using ElevenLabs text-to-speech.

Each phrase is generated once and saved as an MP3 in ~/.cache/rescuebot_voice/,
so repeated alerts play instantly, keep working without internet, and don't use
more ElevenLabs credits. If ElevenLabs can't be reached, espeak-ng speaks
instead; if that is missing too, the alert is only logged.

Needs: pip install 'rescuebot-dashboard[voice]' (the elevenlabs package), the
ELEVENLABS_API_KEY environment variable, ffplay, and a speaker (on the robot, a
MAX98357A I2S amp set as the default audio output).

Test on its own:
    python3 -m rescuebot.voice "Warning. Person detected ahead."
"""

from __future__ import annotations

import hashlib
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

CACHE_DIR = os.path.expanduser("~/.cache/rescuebot_voice")
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George", one of ElevenLabs' premade voices
DEFAULT_MODEL = "eleven_flash_v2_5"      # ElevenLabs' lowest-latency model
CAMERA_HFOV_DEG = 66.0                   # Raspberry Pi AI Camera horizontal field of view
STARTUP_PHRASE = "Rescue bot online. Scanning for survivors."

# How each detection label is spoken. Hazards get "Warning." in front.
SPOKEN = {
    "person": ("Person", False),
    "knife": ("Sharp object", True),
    "scissors": ("Sharp object", True),
    "oven": ("Heat source", True),
    "microwave": ("Heat source", True),
    "toaster": ("Heat source", True),
}
# Which detection to announce when several appear at once (lower comes first).
PRIORITY = {"person": 0, "knife": 1, "scissors": 1, "oven": 2, "microwave": 2, "toaster": 2}


def cache_path(text, voice=DEFAULT_VOICE, model=DEFAULT_MODEL):
    key = hashlib.sha1(f"{voice}|{model}|{text}".encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, key + ".mp3")


def synthesize(text, voice=DEFAULT_VOICE, model=DEFAULT_MODEL):
    """Return the path to an MP3 of `text`, calling ElevenLabs only if it isn't cached yet."""
    path = cache_path(text, voice, model)
    if os.path.exists(path):
        return path
    from elevenlabs.client import ElevenLabs  # imported here so the rest works without the package

    client = ElevenLabs()  # reads ELEVENLABS_API_KEY
    audio = client.text_to_speech.convert(
        text=text, voice_id=voice, model_id=model, output_format="mp3_44100_128")
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    if os.path.getsize(tmp) == 0:
        os.remove(tmp)
        raise RuntimeError("ElevenLabs returned no audio")
    os.replace(tmp, path)  # only a complete file ever gets the cache name
    return path


def play(path):
    """Play an audio file on the default speaker and wait until it finishes."""
    subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path], check=False)


def speak_offline(text):
    """Robotic backup voice for when ElevenLabs can't be reached. Returns False if espeak-ng isn't installed."""
    if not shutil.which("espeak-ng"):
        return False
    subprocess.run(["espeak-ng", text], check=False)
    return True


def x_to_bearing(x_norm, hfov_deg):
    """Horizontal position in the image (0 = left edge, 1 = right edge) -> degrees, right is positive.

    Same formula as tools/sensors/rescue_sensors.py.
    """
    half_width = math.tan(math.radians(hfov_deg / 2))
    return math.degrees(math.atan((x_norm - 0.5) * 2 * half_width))


def with_bearings(detections, hfov_deg=CAMERA_HFOV_DEG):
    """Copy dashboard detections, adding `bearing_deg` from each bounding box's center.

    Dashboard detections carry only normalized boxes, so without this every alert
    would say "ahead". Detections without a usable box are passed through unchanged.
    """
    result = []
    for det in detections:
        det = dict(det)
        bbox = det.get("bbox")
        if "bearing_deg" not in det and isinstance(bbox, dict):
            try:
                det["bearing_deg"] = x_to_bearing(float(bbox["x"]) + float(bbox["width"]) / 2, hfov_deg)
            except (KeyError, TypeError, ValueError):
                pass
        result.append(det)
    return result


def alert_text(detections, say_distance=False):
    """Pick the most important detection and turn it into a short sentence, or None."""
    if not detections:
        return None
    det = min(detections, key=lambda d: (PRIORITY.get(d["label"], 9), -d.get("confidence", 0)))
    name, warn = SPOKEN.get(det["label"], (det["label"].capitalize(), False))
    bearing = det.get("bearing_deg") or 0
    where = "ahead" if abs(bearing) < 10 else ("on the right" if bearing > 0 else "on the left")
    text = f"{'Warning. ' if warn else ''}{name} detected {where}"
    dist = det.get("distance_m")
    if say_distance and dist:
        # Round to half a meter so there are only a few distinct phrases to cache.
        text += f", about {round(dist * 2) / 2:g} meters away"
    return text + "."


def _stderr_log(msg):
    print(msg, file=sys.stderr, flush=True)


class Speaker:
    """Speaks alerts one at a time in the background.

    The same sentence isn't repeated within `cooldown` seconds, and at most a few
    alerts wait in line, so the robot never falls behind talking about old events.
    `synthesizer`, `player`, and `fallback` default to ElevenLabs, ffplay, and
    espeak-ng; tests pass fakes.
    """

    def __init__(self, cooldown=8.0, voice=DEFAULT_VOICE, model=DEFAULT_MODEL, log=None,
                 synthesizer=None, player=None, fallback=None, clock=time.monotonic, max_queue=3):
        self.cooldown = cooldown
        self.voice, self.model = voice, model
        self.log = log or _stderr_log
        self.synthesizer = synthesizer or synthesize
        self.player = player or play
        self.fallback = fallback or speak_offline
        self.clock = clock
        self.last_said = {}
        self.queue = queue.Queue(maxsize=max_queue)
        self.thread = threading.Thread(target=self._run, name="rescuebot-voice", daemon=True)
        self.thread.start()

    def say(self, text):
        """Queue `text` to be spoken. Returns False if it was skipped (repeat or queue full)."""
        if not text:
            return False
        now = self.clock()
        if now - self.last_said.get(text, float("-inf")) < self.cooldown:
            return False
        try:
            self.queue.put_nowait(text)
        except queue.Full:
            return False
        self.last_said[text] = now
        return True

    def prepare(self, texts):
        """Generate phrases ahead of time in the background, so the first alert plays instantly."""
        def work():
            for text in texts:
                try:
                    self.synthesizer(text, self.voice, self.model)
                except Exception as e:
                    self.log(f"[voice] couldn't prepare '{text}': {e}")
                    return
        threading.Thread(target=work, name="rescuebot-voice-prepare", daemon=True).start()

    def _run(self):
        while True:
            text = self.queue.get()
            if text is None:
                return
            try:
                self._speak(text)
            except Exception as e:  # a broken speaker must never kill the thread
                self.log(f"[voice] couldn't speak '{text}' ({type(e).__name__}: {e})")

    def _speak(self, text):
        try:
            path = self.synthesizer(text, self.voice, self.model)
        except Exception as e:
            self.log(f"[voice] ElevenLabs failed ({type(e).__name__}: {e})")
            if not self.fallback(text):
                self.log(f"[voice] (not spoken) {text}")
            return
        self.log(f"[voice] {text}")
        self.player(path)

    def close(self, timeout=10):
        """Let the current alert finish, then stop."""
        try:
            self.queue.put(None, timeout=1)
        except queue.Full:
            pass
        self.thread.join(timeout)


class CameraVoice:
    """Speaks camera detections on its own thread.

    About twice a second it reads `camera_status()` (the dashboard's camera status
    dict), adds bearings from the boxes, and passes the alert to `speaker.say()`,
    which never blocks. It shares nothing with the control loop, so it can't delay
    Stop, and any error is logged and skipped.
    """

    def __init__(self, camera_status: Callable[[], dict], speaker: Speaker,
                 interval_s=0.5, hfov_deg=CAMERA_HFOV_DEG, log=None):
        self.camera_status = camera_status
        self.speaker = speaker
        self.interval_s = interval_s
        self.hfov_deg = hfov_deg
        self.log = log or _stderr_log
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rescuebot-camera-voice", daemon=True)

    def start(self):
        self._thread.start()

    def poll_once(self):
        """Speak the current detections, if any. Returns the text passed to the speaker, or None."""
        status = self.camera_status()
        if status.get("status") != "online":
            return None
        text = alert_text(with_bearings(status.get("detections") or [], self.hfov_deg))
        if text:
            self.speaker.say(text)
        return text

    def _run(self):
        while not self._stop.wait(self.interval_s):
            try:
                self.poll_once()
            except Exception as e:
                self.log(f"[voice] camera poll failed ({type(e).__name__}: {e})")

    def close(self, timeout=2.0):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)
        self.speaker.close(timeout)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    phrase = " ".join(args) or "Rescue bot online. Scanning for hazards."
    try:
        path = synthesize(phrase)
    except Exception as e:
        sys.exit(f"ElevenLabs failed: {type(e).__name__}: {e}")
    print(f"Saved {path}")
    play(path)


if __name__ == "__main__":
    main()
