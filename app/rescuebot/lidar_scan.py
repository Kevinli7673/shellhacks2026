"""RPLIDAR C1 reader for the dashboard, run as its own process.

Runs Slamtec's ultra_simple (the same SDK tool tools/sensors/rescue_sensors.py
uses), groups its points into 360-degree scans, and prints each scan reduced to
one nearest distance per degree as a JSON line:

    {"type": "lidar_scan", "bins": [mm or 0, ... 360 values], "points": N,
     "nearest": {"angle": deg, "mm": mm} or null, "scans": total}

Bin i covers angles [i, i+1) in robot coordinates: 0 is straight ahead (the
camera end), angles grow clockwise seen from above, and --offset corrects for
how the LiDAR is mounted. 0 means no reading in that degree. Scans are
rate-limited so the dashboard gets at most --max-rate per second.

This process only reads the LiDAR. It never opens the ESP32-S2 port: the
default device is the LiDAR's CP2102N adapter by its stable by-id path.

    python -m rescuebot.lidar_scan
    python -m rescuebot.lidar_scan --port /dev/serial/by-id/usb-Silicon_Labs_CP2102N... --offset 180
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import Iterable, Sequence


# Output line from ultra_simple, e.g. "S  theta: 12.34 Dist: 01234.00 Q: 47 "
LIDAR_LINE = re.compile(r"theta:\s*([0-9.]+)\s+Dist:\s*([0-9.]+)\s+Q:\s*(\d+)")
DEFAULT_BIN = "~/rplidar_sdk/output/Linux/Release/ultra_simple"
DEFAULT_PORT_GLOB = "/dev/serial/by-id/*CP2102N*"
DEFAULT_BAUD = 460800
BIN_COUNT = 360
MAX_RANGE_MM = 12000  # RPLIDAR C1 rated range; farther readings are noise


def parse_line(line: str) -> tuple[float, float, int] | None:
    """Return (theta_deg, dist_mm, quality) from an ultra_simple line, or None."""
    match = LIDAR_LINE.search(line)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2)), int(match.group(3))


class ScanAssembler:
    """Group points into full scans. A new scan starts when the angle wraps back down."""

    def __init__(self) -> None:
        self.points: list[tuple[float, float]] = []
        self.last_theta: float | None = None

    def add(self, theta: float, dist: float, quality: int) -> list[tuple[float, float]] | None:
        done = None
        if self.last_theta is not None and theta < self.last_theta - 180 and self.points:
            done, self.points = self.points, []
        self.last_theta = theta
        if 0 < dist <= MAX_RANGE_MM and quality > 0:
            self.points.append((theta, dist))
        return done


def bin_scan(points: Iterable[tuple[float, float]], offset_deg: float = 0.0) -> list[int]:
    """Reduce a scan to the nearest distance (mm) per degree in robot coordinates."""
    bins = [0] * BIN_COUNT
    for theta, dist in points:
        index = int((theta + offset_deg) % 360) % BIN_COUNT
        mm = int(round(dist))
        if bins[index] == 0 or mm < bins[index]:
            bins[index] = mm
    return bins


def nearest(bins: Sequence[int]) -> dict[str, int] | None:
    readings = [(mm, angle) for angle, mm in enumerate(bins) if mm > 0]
    if not readings:
        return None
    mm, angle = min(readings)
    return {"angle": angle, "mm": mm}


def scan_record(bins: list[int], point_count: int, scans: int) -> dict[str, object]:
    return {
        "type": "lidar_scan",
        "bins": bins,
        "points": point_count,
        "nearest": nearest(bins),
        "scans": scans,
    }


def emit(record: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def message(text: str) -> None:
    emit({"type": "sensor_message", "message": text})


def default_port() -> str | None:
    matches = sorted(glob.glob(DEFAULT_PORT_GLOB))
    return matches[0] if matches else None


def build_command(binary: str, port: str, baud: int) -> list[str]:
    cmd = [binary, "--channel", "--serial", port, str(baud)]
    # stdbuf makes ultra_simple print each line at once instead of in large chunks.
    stdbuf = shutil.which("stdbuf")
    return [stdbuf, "-oL", *cmd] if stdbuf else cmd


def run(lines: Iterable[str], offset_deg: float, max_rate: float) -> None:
    assembler = ScanAssembler()
    min_interval = 1.0 / max_rate if max_rate > 0 else 0.0
    last_emit = 0.0
    last_note = 0.0
    scans = 0
    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            # Until scans arrive, pass ultra_simple's own words (connection
            # errors, health status) to the dashboard, at most once a second.
            text = line.strip()
            now = time.monotonic()
            if text and scans == 0 and now - last_note >= 1.0:
                last_note = now
                message(f"LiDAR tool: {text[:160]}")
            continue
        done = assembler.add(*parsed)
        if done is None:
            continue
        scans += 1
        now = time.monotonic()
        if now - last_emit < min_interval:
            continue
        last_emit = now
        emit(scan_record(bin_scan(done, offset_deg), len(done), scans))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stream RPLIDAR scans for the Rescuebot dashboard.")
    parser.add_argument("--bin", default=os.environ.get("RESCUEBOT_LIDAR_BIN", DEFAULT_BIN),
                        help="ultra_simple from the Slamtec rplidar_sdk (default: %(default)s)")
    parser.add_argument("--port", default=os.environ.get("RESCUEBOT_LIDAR_PORT"),
                        help=f"LiDAR serial device (default: first match of {DEFAULT_PORT_GLOB})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--offset", type=float, default=float(os.environ.get("RESCUEBOT_LIDAR_OFFSET", "0")),
                        help="degrees added to LiDAR angles so 0 points at the robot's front")
    parser.add_argument("--max-rate", type=float, default=5.0, help="maximum scans printed per second")
    parser.add_argument("--stdin", action="store_true",
                        help="read ultra_simple output from stdin instead of starting it (for testing)")
    args = parser.parse_args(argv)

    if args.stdin:
        run(sys.stdin, args.offset, args.max_rate)
        return 0

    binary = os.path.expanduser(args.bin)
    if not os.path.exists(binary):
        message(f"LiDAR tool not found: {binary}. Build the Slamtec rplidar_sdk or set RESCUEBOT_LIDAR_BIN.")
        return 2
    port = args.port or default_port()
    if port is None:
        message("No LiDAR found: nothing matches /dev/serial/by-id/*CP2102N*. Check the LiDAR USB cable.")
        return 2

    child = subprocess.Popen(
        build_command(binary, port, args.baud),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # its errors explain a missing scan
        stdin=subprocess.DEVNULL,
        text=True,
        errors="replace",
        bufsize=1,
    )

    def stop(_signum: int, _frame: object) -> None:
        # ultra_simple stops the LiDAR motor on SIGINT.
        if child.poll() is None:
            child.send_signal(signal.SIGINT)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    message(f"Waiting for scans from {port}.")
    assert child.stdout is not None
    try:
        run(child.stdout, args.offset, args.max_rate)
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(3)
            except subprocess.TimeoutExpired:
                child.kill()
    code = child.wait()
    message(f"LiDAR tool exited (code {code}). Check the LiDAR cable and power.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
