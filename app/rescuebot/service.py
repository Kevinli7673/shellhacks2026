"""Application control service independent of FastAPI transport details."""

from __future__ import annotations

import time

from .control import ControlSnapshot, ManualControl
from .mock import MockMotorBackend


class RobotControlService:
    """Connect browser commands to the mock motor backend at a fixed cadence."""

    def __init__(self, control: ManualControl | None = None, backend: MockMotorBackend | None = None) -> None:
        self.control = control or ManualControl()
        self.backend = backend or MockMotorBackend()
        self._last_snapshot: ControlSnapshot | None = None

    def tick(self, now: float | None = None) -> ControlSnapshot:
        snapshot = self.control.snapshot(now)
        reason = snapshot.fault or ("manual" if snapshot.armed else "disarmed")
        self.backend.apply(snapshot.wheels, reason)
        self._last_snapshot = snapshot
        return snapshot

    def claim(self, session: str) -> bool:
        return self.control.claim(session)

    def enable(self, session: str, now: float | None = None) -> bool:
        return self.control.enable(session, now, backend_healthy=self.backend.healthy)

    def keys(self, session: str, keys: list[str], now: float | None = None) -> bool:
        accepted = self.control.set_keys(session, keys, now)
        self.tick(time.monotonic() if now is None else now)
        return accepted

    def adjust_speed(self, session: str, delta_percent: int) -> bool:
        accepted = self.control.adjust_speed(session, delta_percent)
        self.tick()
        return accepted

    def stop(self, reason: str = "operator_stop") -> None:
        self.control.stop(reason)
        self.tick()

    def disconnect(self, session: str) -> None:
        self.control.disconnect(session)
        self.tick()

    def state(self) -> dict[str, object]:
        snapshot = self.tick()
        return {
            "control": snapshot.as_dict(),
            "motor": self.backend.as_dict(),
            "camera": {
                "backend": "mock",
                "status": "offline",
                "message": "Camera service has not been integrated.",
            },
        }
