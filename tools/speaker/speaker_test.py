#!/usr/bin/env python3
"""
Speaker test for the rescue robot (Raspberry Pi 5 + MAX98357A + speaker).

Run after speaker_setup.sh and a reboot:
    python3 speaker_test.py
    python3 speaker_test.py --say "Any text you want"

It will:
  1. Find the MAX98357A sound card
  2. Play three beeps (low, middle, high)
  3. Play the "victim found" alert
  4. Speak "I am a rescue robot. Help is on the way."
"""
import argparse
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

RATE = 44100
VOLUME = 1.0          # 0.0 - 1.0  (the amp has no volume knob, so set it here)


def find_card():
    """Return the ALSA card number of the MAX98357A, or None."""
    out = subprocess.run(["aplay", "-l"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "max98357" in line.lower() or "hifiberry" in line.lower():
            m = re.search(r"card (\d+)", line)
            if m:
                return int(m.group(1))
    return None


def make_tone(path, notes):
    """notes = list of (frequency_hz, seconds). 0 Hz = silence."""
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = bytearray()
        for freq, secs in notes:
            n = int(RATE * secs)
            for i in range(n):
                fade = min(1.0, i / 400, (n - i) / 400)   # tiny fade = no clicks
                v = 0 if freq == 0 else math.sin(2 * math.pi * freq * i / RATE)
                frames += struct.pack("<h", int(v * fade * VOLUME * 32767))
        w.writeframes(bytes(frames))


def play(path, card):
    subprocess.run(["aplay", "-q", "-D", f"plughw:{card},0", path], check=True)


def say(text, card, tmpdir):
    if not shutil.which("espeak-ng"):
        print("  espeak-ng not installed. Run: sudo apt install -y espeak-ng")
        return
    path = os.path.join(tmpdir, "voice.wav")
    amp = str(int(200 * VOLUME))                   # espeak volume 0-200
    subprocess.run(["espeak-ng", "-s", "150", "-a", amp, "-w", path, text], check=True)
    play(path, card)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--say", help="just speak this text and exit")
    args = parser.parse_args()

    card = find_card()
    if card is None:
        print("MAX98357A not found in 'aplay -l'.")
        print("  - Did you run speaker_setup.sh and reboot?")
        print("  - Check: grep max98357a /boot/firmware/config.txt")
        sys.exit(1)
    print(f"Found MAX98357A on card {card}")

    with tempfile.TemporaryDirectory() as tmp:
        if args.say:
            say(args.say, card, tmp)
            return

        print("1) Three beeps: low, middle, high")
        beeps = os.path.join(tmp, "beeps.wav")
        make_tone(beeps, [(440, 0.3), (0, 0.15), (880, 0.3), (0, 0.15), (1320, 0.3)])
        play(beeps, card)

        print("2) Victim-found alert")
        alert = os.path.join(tmp, "alert.wav")
        make_tone(alert, [(1000, 0.12), (0, 0.08)] * 3 + [(1500, 0.4)])
        play(alert, card)

        print("3) Robot voice")
        say("I am a rescue robot. Help is on the way.", card, tmp)

    print("\nDone! If it was too quiet, raise VOLUME at the top of this file,")
    print("or wire the amp's GAIN pin to GND for more loudness.")


if __name__ == "__main__":
    main()
