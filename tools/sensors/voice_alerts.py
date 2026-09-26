#!/usr/bin/env python3
"""Spoken alerts for the rescuebot, using ElevenLabs text-to-speech.

Each phrase is generated once and saved as an MP3 in ~/.cache/rescuebot_voice/,
so repeated alerts play instantly, keep working without internet, and don't use
more ElevenLabs credits.

Needs: pip install elevenlabs, the ELEVENLABS_API_KEY environment variable,
and a speaker (the Pi 5 has no headphone jack: use USB, Bluetooth, or HDMI audio).

Test on its own:
    python3 voice_alerts.py "Warning. Person detected ahead."
"""

import hashlib
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

CACHE_DIR = os.path.expanduser("~/.cache/rescuebot_voice")
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George", one of ElevenLabs' premade voices
DEFAULT_MODEL = "eleven_flash_v2_5"      # ElevenLabs' lowest-latency model

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


class Speaker:
    """Speaks alerts one at a time in the background.

    The same sentence isn't repeated within `cooldown` seconds, and at most a few
    alerts wait in line, so the robot never falls behind talking about old events.
    """

    def __init__(self, cooldown=8.0, voice=DEFAULT_VOICE, model=DEFAULT_MODEL, log=None):
        self.cooldown = cooldown
        self.voice, self.model = voice, model
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.last_said = {}
        self.queue = queue.Queue(maxsize=3)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def say(self, text):
        """Queue `text` to be spoken. Returns False if it was skipped (repeat or queue full)."""
        if not text:
            return False
        now = time.monotonic()
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
                    synthesize(text, self.voice, self.model)
                except Exception as e:
                    self.log(f"[voice] couldn't prepare '{text}': {e}")
                    return
        threading.Thread(target=work, daemon=True).start()

    def _run(self):
        while True:
            text = self.queue.get()
            if text is None:
                return
            try:
                path = synthesize(text, self.voice, self.model)
            except Exception as e:
                self.log(f"[voice] ElevenLabs failed ({type(e).__name__}: {e})")
                if not speak_offline(text):
                    self.log(f"[voice] (not spoken) {text}")
                continue
            self.log(f"[voice] {text}")
            play(path)

    def close(self, timeout=10):
        """Let the current alert finish, then stop."""
        try:
            self.queue.put(None, timeout=1)
        except queue.Full:
            pass
        self.thread.join(timeout)


if __name__ == "__main__":
    phrase = " ".join(sys.argv[1:]) or "Rescue bot online. Scanning for hazards."
    try:
        path = synthesize(phrase)
    except Exception as e:
        sys.exit(f"ElevenLabs failed: {type(e).__name__}: {e}")
    print(f"Saved {path}")
    play(path)
