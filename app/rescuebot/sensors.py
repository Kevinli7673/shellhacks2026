"""Auxiliary sensors on the dashboard: USB webcam video, its microphone level, and LiDAR.

Each live sensor runs as its own child process (see sensor_process.py), so a
missing or failing device only changes that sensor's status. Nothing here can
reach the control service, motor bridge, or ESP32-S2.

Modes:
    off   no auxiliary sensors (the default; the dashboard hides the panels)
    mock  synthetic readings for developing and testing the dashboard
    live  webcam, microphone, and LiDAR child processes on the Raspberry Pi
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Protocol

from .sensor_process import SensorProcess
from .webcam_stream import DEFAULT_PORT as DEFAULT_WEBCAM_PORT


# The directory holding this rescuebot package, so sensor children import the
# same code as the dashboard even when another copy is installed.
PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])

# A 100 ms window louder than this counts as hearing sound. A quiet room with
# the Brio microphone is typically well below it; speech is well above it.
HEARING_THRESHOLD_DBFS = -50.0


class Sensors(Protocol):
    mode: str

    def start(self) -> None: ...
    def close(self) -> None: ...
    def status(self) -> dict[str, Any]: ...
    def lidar(self) -> dict[str, Any]: ...


def _lidar_summary(status: str, message: str, record: dict[str, Any] | None, age_ms: int | None) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "age_ms": age_ms,
        "nearest": None if record is None else record.get("nearest"),
        "points": None if record is None else record.get("points"),
    }


def _audio_summary(status: str, message: str, record: dict[str, Any] | None, age_ms: int | None) -> dict[str, Any]:
    rms = None if record is None else record.get("rms_dbfs")
    peak = None if record is None else record.get("peak_dbfs")
    return {
        "status": status,
        "message": message,
        "age_ms": age_ms,
        "rms_dbfs": rms,
        "peak_dbfs": peak,
        "hearing": status == "online" and isinstance(rms, (int, float)) and rms > HEARING_THRESHOLD_DBFS,
        "threshold_dbfs": HEARING_THRESHOLD_DBFS,
        "device": None if record is None else record.get("device"),
    }


class OffSensors:
    mode = "off"

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode}

    def lidar(self) -> dict[str, Any]:
        return {"status": "offline", "message": "LiDAR is not enabled.", "bins": None}


class LiveSensors:
    mode = "live"

    def __init__(self, *, python: str = sys.executable, webcam_port: int = DEFAULT_WEBCAM_PORT) -> None:
        self.webcam_port = webcam_port
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [PACKAGE_ROOT, env.get("PYTHONPATH")]))
        self.webcam = SensorProcess(
            "Webcam",
            [python, "-u", "-m", "rescuebot.webcam_stream", "--port", str(webcam_port)],
            "webcam_status",
            expiry_s=3.0,
            starting_message="Starting the webcam.",
            env=env,
        )
        self.audio = SensorProcess(
            "Microphone",
            [python, "-u", "-m", "rescuebot.audio_level"],
            "audio_level",
            expiry_s=1.0,
            starting_message="Starting the microphone.",
            env=env,
        )
        self.lidar_process = SensorProcess(
            "LiDAR",
            [python, "-u", "-m", "rescuebot.lidar_scan"],
            "lidar_scan",
            expiry_s=2.0,
            starting_message="Starting the LiDAR.",
            env=env,
        )

    def _all(self) -> tuple[SensorProcess, ...]:
        return (self.webcam, self.audio, self.lidar_process)

    def start(self) -> None:
        for process in self._all():
            process.start()

    def close(self) -> None:
        for process in self._all():
            process.close()

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        webcam_status, webcam_message, webcam, webcam_age = self.webcam.snapshot(now)
        return {
            "mode": self.mode,
            "webcam": {
                "status": webcam_status,
                "message": webcam_message,
                "age_ms": webcam_age,
                "fps": None if webcam is None else webcam.get("fps"),
                "width": None if webcam is None else webcam.get("width"),
                "height": None if webcam is None else webcam.get("height"),
                "video": (
                    {"port": self.webcam_port, "path": "/stream.mjpg"}
                    if webcam_status != "offline" else None
                ),
            },
            "audio": _audio_summary(*self.audio.snapshot(now)),
            "lidar": _lidar_summary(*self.lidar_process.snapshot(now)),
        }

    def lidar(self) -> dict[str, Any]:
        status, message, record, age_ms = self.lidar_process.snapshot()
        return {
            **_lidar_summary(status, message, record, age_ms),
            "bins": None if record is None or status == "offline" else record.get("bins"),
        }


class MockSensors:
    """Deterministic synthetic readings: a 4 m x 3 m room and a pulsing voice level."""

    mode = "mock"

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def _elapsed(self) -> float:
        return self._clock() - self._start

    def _bins(self) -> list[int]:
        # Robot 1.2 m from the left wall and 1.8 m from the front wall of a 4 m x 3 m room,
        # with a 40 cm box ahead and to the right.
        bins = []
        for angle in range(360):
            theta = math.radians(angle + 0.5)
            dx, dy = math.sin(theta), math.cos(theta)  # x right, y forward
            limits = []
            if dy > 1e-6:
                limits.append(1800 / dy)
            if dy < -1e-6:
                limits.append(-1200 / dy)
            if dx > 1e-6:
                limits.append(2800 / dx)
            if dx < -1e-6:
                limits.append(-1200 / dx)
            distance = min(limits)
            if 25 <= angle <= 38:
                distance = min(distance, 900)
            bins.append(int(distance) if distance <= 12000 else 0)
        return bins

    def status(self) -> dict[str, Any]:
        elapsed = self._elapsed()
        rms = round(-62 + 22 * max(0.0, math.sin(elapsed * 1.7)) ** 2, 1)
        record = {"rms_dbfs": rms, "peak_dbfs": round(min(0.0, rms + 12), 1), "device": "mock"}
        bins = self._bins()
        nearest = min((mm, angle) for angle, mm in enumerate(bins) if mm > 0)
        return {
            "mode": self.mode,
            "webcam": {
                "status": "offline",
                "message": "Mock sensors have no webcam video.",
                "age_ms": None,
                "fps": None,
                "width": None,
                "height": None,
                "video": None,
            },
            "audio": _audio_summary("online", "Mock microphone level.", record, 40),
            "lidar": _lidar_summary(
                "online", "Mock LiDAR room.", {"nearest": {"angle": nearest[1], "mm": nearest[0]}, "points": 360}, 120
            ),
        }

    def lidar(self) -> dict[str, Any]:
        bins = self._bins()
        nearest = min((mm, angle) for angle, mm in enumerate(bins) if mm > 0)
        return {
            **_lidar_summary("online", "Mock LiDAR room.", {"nearest": {"angle": nearest[1], "mm": nearest[0]}, "points": 360}, 120),
            "bins": bins,
        }


def create_sensors(mode: str, *, webcam_port: int = DEFAULT_WEBCAM_PORT) -> Sensors:
    if mode == "off":
        return OffSensors()
    if mode == "mock":
        return MockSensors()
    if mode == "live":
        return LiveSensors(webcam_port=webcam_port)
    raise ValueError("sensors must be off, mock, or live")
