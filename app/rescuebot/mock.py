"""In-memory motor backend used until the ESP32 serial backend is integrated."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time

from .mecanum import WheelOutputs

if False:  # pragma: no cover - import only for static type checkers
    from .control import ControlSnapshot


@dataclass(frozen=True)
class MockMotorEvent:
    timestamp: float
    wheels: WheelOutputs
    reason: str


class MockMotorBackend:
    """Stores the newest requested wheel state and a small, inspectable history."""

    def __init__(self, history_size: int = 100) -> None:
        self.healthy = True
        self.wheels = WheelOutputs.stopped()
        self.last_reason = "uninitialized"
        self.events: deque[MockMotorEvent] = deque(maxlen=history_size)
        self.accessories = {"buzzer": False, "light": False}
        self.apply(self.wheels, "boot")

    def set_accessories(self, buzzer: bool, light: bool) -> None:
        self.accessories = {"buzzer": buzzer, "light": light}

    def apply(self, wheels: WheelOutputs, reason: str = "manual") -> None:
        if wheels == self.wheels and reason == self.last_reason:
            return
        self.wheels = wheels
        self.last_reason = reason
        self.events.append(MockMotorEvent(time.monotonic(), wheels, reason))

    def apply_snapshot(self, snapshot: "ControlSnapshot", reason: str, now: float | None = None) -> None:
        """Match simulation backends without changing mock behavior."""
        self.apply(snapshot.wheels, reason)

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": "mock",
            "healthy": self.healthy,
            "wheels": self.wheels.as_dict(),
            "last_reason": self.last_reason,
            "event_count": len(self.events),
            "accessories": {**self.accessories, "available": self.healthy},
        }
