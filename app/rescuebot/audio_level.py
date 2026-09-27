"""Microphone level meter for the dashboard, run as its own process.

Captures the webcam microphone with ALSA's arecord and prints its level every
100 ms as a JSON line. The audio itself is never stored or sent anywhere; only
the level leaves this process:

    {"type": "audio_level", "rms_dbfs": -42.1, "peak_dbfs": -30.5, "device": "plughw:CARD=BRIO,DEV=0"}

dBFS is decibels relative to digital full scale: 0 is the loudest possible
signal, silence is around -90, and a quiet room is typically -60 to -45.

    python -m rescuebot.audio_level
    python -m rescuebot.audio_level --device plughw:CARD=BRIO,DEV=0
"""

from __future__ import annotations

import argparse
from array import array
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
from typing import BinaryIO


SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
FLOOR_DBFS = -90.0
FULL_SCALE = 32768.0
# Words that identify a USB webcam's sound card in /proc/asound/cards.
WEBCAM_CARD_WORDS = ("brio", "logitech", "webcam", "camera", "c920", "c922", "c930")
CARD_LINE = re.compile(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(.*)$")


def to_dbfs(value: float) -> float:
    if value <= 0:
        return FLOOR_DBFS
    return max(FLOOR_DBFS, round(20 * math.log10(value / FULL_SCALE), 1))


def chunk_levels(data: bytes) -> tuple[float, float]:
    """Return (rms_dbfs, peak_dbfs) for little-endian signed 16-bit mono samples."""
    samples = array("h")
    samples.frombytes(data[: len(data) - len(data) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return FLOOR_DBFS, FLOOR_DBFS
    peak = max(abs(sample) for sample in samples)
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return to_dbfs(rms), to_dbfs(peak)


def find_webcam_card(cards_text: str) -> str | None:
    """Pick the webcam's ALSA card id from the contents of /proc/asound/cards."""
    for line in cards_text.splitlines():
        match = CARD_LINE.match(line)
        if not match:
            continue
        card_id, description = match.group(2), match.group(3).lower()
        if any(word in (card_id.lower() + " " + description) for word in WEBCAM_CARD_WORDS):
            return card_id
    return None


def default_device() -> str | None:
    try:
        cards = Path("/proc/asound/cards").read_text(encoding="utf-8")
    except OSError:
        return None
    card = find_webcam_card(cards)
    return None if card is None else f"plughw:CARD={card},DEV=0"


def emit(record: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def run(stream: BinaryIO, device: str) -> None:
    chunk_bytes = int(SAMPLE_RATE * CHUNK_SECONDS) * 2
    while True:
        data = stream.read(chunk_bytes)
        if not data:
            return
        rms, peak = chunk_levels(data)
        emit({"type": "audio_level", "rms_dbfs": rms, "peak_dbfs": peak, "device": device})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report the webcam microphone level for the dashboard.")
    parser.add_argument("--device", default=os.environ.get("RESCUEBOT_AUDIO_DEVICE"),
                        help="ALSA capture device (default: the webcam's card from /proc/asound/cards)")
    parser.add_argument("--stdin", action="store_true",
                        help="read raw S16_LE mono samples from stdin instead of arecord (for testing)")
    args = parser.parse_args(argv)

    if args.stdin:
        run(sys.stdin.buffer, "stdin")
        return 0

    device = args.device or default_device()
    if device is None:
        emit({"type": "sensor_message",
              "message": "No webcam microphone found in /proc/asound/cards. Check the webcam USB cable."})
        return 2
    if shutil.which("arecord") is None:
        emit({"type": "sensor_message", "message": "arecord is not installed (sudo apt install alsa-utils)."})
        return 2

    child = subprocess.Popen(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )

    def stop(_signum: int, _frame: object) -> None:
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    assert child.stdout is not None and child.stderr is not None
    try:
        run(child.stdout, device)
    finally:
        if child.poll() is None:
            child.terminate()
    code = child.wait()
    error = child.stderr.read().decode("utf-8", "replace").strip().splitlines()
    detail = error[-1][:120] if error else f"exit code {code}"
    emit({"type": "sensor_message", "message": f"Microphone capture stopped on {device}: {detail}"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
