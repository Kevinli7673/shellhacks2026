"""LiDAR on the dashboard.

The LiDAR runs as its own child process (see sensor_process.py), so a missing or
failing device only changes its own status. Nothing here can reach the control
service, motor bridge, or ESP32-S2.

Modes:
    off   no LiDAR (the default; the dashboard hides the panel)
    mock  a synthetic room for developing and testing the dashboard
    live  the rescuebot.lidar_scan child process on the Raspberry Pi
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Protocol

from .sensor_process import SensorProcess


# The directory holding this rescuebot package, so the child imports the same
# code as the dashboard even when another copy is installed.
PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])


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

    def __init__(self, *, python: str = sys.executable) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [PACKAGE_ROOT, env.get("PYTHONPATH")]))
        self.lidar_process = SensorProcess(
            "LiDAR",
            [python, "-u", "-m", "rescuebot.lidar_scan"],
            "lidar_scan",
            expiry_s=2.0,
            starting_message="Starting the LiDAR.",
            env=env,
        )

    def start(self) -> None:
        self.lidar_process.start()

    def close(self) -> None:
        self.lidar_process.close()

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode, "lidar": _lidar_summary(*self.lidar_process.snapshot())}

    def lidar(self) -> dict[str, Any]:
        status, message, record, age_ms = self.lidar_process.snapshot()
        return {
            **_lidar_summary(status, message, record, age_ms),
            "bins": None if record is None or status == "offline" else record.get("bins"),
        }


def mock_room_bins() -> list[int]:
    """A 4 m x 3 m room: robot 1.2 m from the left wall and 1.8 m from the front
    wall, with a 40 cm box ahead and to the right."""
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


class MockSensors:
    """A deterministic synthetic room."""

    mode = "mock"

    def __init__(self) -> None:
        self._bins = mock_room_bins()
        mm, angle = min((mm, angle) for angle, mm in enumerate(self._bins) if mm > 0)
        self._record = {"nearest": {"angle": angle, "mm": mm}, "points": 360}

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode, "lidar": _lidar_summary("online", "Mock LiDAR room.", self._record, 120)}

    def lidar(self) -> dict[str, Any]:
        return {**_lidar_summary("online", "Mock LiDAR room.", self._record, 120), "bins": list(self._bins)}


def create_sensors(mode: str) -> Sensors:
    if mode == "off":
        return OffSensors()
    if mode == "mock":
        return MockSensors()
    if mode == "live":
        return LiveSensors()
    raise ValueError("sensors must be off, mock, or live")
