"""Keyboard intent, single-browser control ownership, and safety deadlines."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Iterable

from .mecanum import WheelOutputs, mix_mecanum


MOVEMENT_KEYS = frozenset(
    {"KeyW", "KeyS", "KeyA", "KeyD", "ArrowLeft", "ArrowRight"}
)


@dataclass(frozen=True)
class DriveIntent:
    forward: float = 0.0
    sideways: float = 0.0
    turn: float = 0.0

    @classmethod
    def from_keys(cls, keys: Iterable[str]) -> "DriveIntent":
        held = frozenset(keys)
        return cls(
            forward=float(("KeyW" in held) - ("KeyS" in held)),
            sideways=float(("KeyD" in held) - ("KeyA" in held)),
            turn=float(("ArrowRight" in held) - ("ArrowLeft" in held)),
        )

    @property
    def is_stopped(self) -> bool:
        return self.forward == self.sideways == self.turn == 0.0


@dataclass(frozen=True)
class ControlSnapshot:
    owner_session: str | None
    armed: bool
    fault: str | None
    speed_percent: int
    speed_limit: int
    intent: DriveIntent
    wheels: WheelOutputs
    browser_age_ms: int | None
    source: str = "manual"

    def as_dict(self) -> dict[str, object]:
        return {
            "owner_session": self.owner_session,
            "armed": self.armed,
            "fault": self.fault,
            "speed_percent": self.speed_percent,
            "speed_limit": self.speed_limit,
            "intent": {
                "forward": self.intent.forward,
                "sideways": self.intent.sideways,
                "turn": self.intent.turn,
            },
            "wheels": self.wheels.as_dict(),
            "browser_age_ms": self.browser_age_ms,
            "source": self.source,
        }


@dataclass
class ManualControl:
    """Arbitrates one browser's manual input with fail-safe disarming."""

    pwm_ceiling: int = 180
    input_timeout_s: float = 0.250
    speed_percent: int = 30
    owner_session: str | None = None
    armed: bool = False
    fault: str | None = None
    _keys: set[str] = field(default_factory=set)
    _last_input_at: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.pwm_ceiling, int) or not 0 < self.pwm_ceiling <= 255:
            raise ValueError("pwm_ceiling must be an integer between 1 and 255")
        if self.input_timeout_s <= 0:
            raise ValueError("input_timeout_s must be positive")
        self.speed_percent = self._bound_speed(self.speed_percent)

    @staticmethod
    def _bound_speed(speed_percent: int) -> int:
        return max(10, min(100, int(speed_percent)))

    @property
    def speed_limit(self) -> int:
        return round(self.pwm_ceiling * self.speed_percent / 100)

    @property
    def has_movement(self) -> bool:
        return bool(self._keys)

    def claim(self, session: str) -> bool:
        if self.owner_session in (None, session):
            self.owner_session = session
            return True
        return False

    def enable(self, session: str, now: float | None = None, backend_healthy: bool = True) -> bool:
        if session != self.owner_session:
            return False
        if self._keys or not backend_healthy:
            self.armed = False
            self.fault = "backend_unavailable" if not backend_healthy else "release_keys_before_enable"
            return False
        self.armed = True
        self.fault = None
        self._last_input_at = time.monotonic() if now is None else now
        return True

    def set_keys(self, session: str, keys: Iterable[str], now: float | None = None) -> bool:
        if session != self.owner_session:
            return False
        received = set(keys)
        if not received.issubset(MOVEMENT_KEYS):
            self.stop("invalid_keys")
            return False
        self._keys = received
        self._last_input_at = time.monotonic() if now is None else now
        return True

    def adjust_speed(self, session: str, delta_percent: int) -> bool:
        if session != self.owner_session or delta_percent not in (-10, 10):
            return False
        self.speed_percent = self._bound_speed(self.speed_percent + delta_percent)
        return True

    def stop(self, reason: str = "operator_stop") -> None:
        self._keys.clear()
        self.armed = False
        self.fault = reason

    def disconnect(self, session: str) -> None:
        if session == self.owner_session:
            self.stop("browser_disconnected")
            self.owner_session = None
            self._last_input_at = None

    def snapshot(self, now: float | None = None) -> ControlSnapshot:
        current_time = time.monotonic() if now is None else now
        browser_age_ms: int | None = None
        if self._last_input_at is not None:
            browser_age_ms = max(0, round((current_time - self._last_input_at) * 1000))
            if self.armed and current_time - self._last_input_at > self.input_timeout_s:
                self.stop("browser_timeout")

        intent = DriveIntent.from_keys(self._keys) if self.armed else DriveIntent()
        wheels = (
            mix_mecanum(
                intent.forward,
                intent.sideways,
                intent.turn,
                self.speed_limit,
            )
            if self.armed
            else WheelOutputs.stopped()
        )
        return ControlSnapshot(
            owner_session=self.owner_session,
            armed=self.armed,
            fault=self.fault,
            speed_percent=self.speed_percent,
            speed_limit=self.speed_limit,
            intent=intent,
            wheels=wheels,
            browser_age_ms=browser_age_ms,
        )
