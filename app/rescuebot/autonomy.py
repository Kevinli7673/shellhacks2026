"""Latest-only autonomous motion source for the host command arbiter.

The source holds normalized logical motion only.  It has no serial, wheel
mixing, or motor-control dependency, so physical transport continues to be
owned solely by the existing bridge and ESP32 stack.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import secrets
import time

from .control import DriveIntent


AUTONOMY_TIMEOUT_S = 0.250
DEFAULT_AUTONOMY_SPEED_PERCENT = 20
# Real motors stall at low PWM; autonomy scales weak commands up to this.
DEFAULT_AUTONOMY_MIN_PWM = 45


def _motion_axis(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError(f"{name} must be finite and between -1 and 1")
    return value


def _mission(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("mission must be a short non-empty string")
    return value


@dataclass(frozen=True)
class AutonomyIntent:
    """One normalized command from the ROS autonomy adapter."""

    mission: str
    seq: int
    expires_at: float
    forward: float
    sideways: float
    turn: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "mission", _mission(self.mission))
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq <= 0:
            raise ValueError("seq must be a positive integer")
        if isinstance(self.expires_at, bool) or not isinstance(self.expires_at, (int, float)):
            raise ValueError("expires_at must be a finite number")
        if not math.isfinite(float(self.expires_at)) or self.expires_at < 0.0:
            raise ValueError("expires_at must be a finite non-negative number")
        object.__setattr__(self, "expires_at", float(self.expires_at))
        for name in ("forward", "sideways", "turn"):
            object.__setattr__(self, name, _motion_axis(getattr(self, name), name))

    @property
    def motion(self) -> DriveIntent:
        return DriveIntent(self.forward, self.sideways, self.turn)

    def is_fresh(self, now: float) -> bool:
        return now < self.expires_at


@dataclass(frozen=True)
class AutonomyStatus:
    active: bool
    mission: str | None
    reason: str | None


class AutonomyControl:
    """Mission-gated, expiring autonomy state with no implicit restart."""

    def __init__(self, timeout_s: float = AUTONOMY_TIMEOUT_S) -> None:
        if timeout_s <= 0.0:
            raise ValueError("timeout_s must be positive")
        self.timeout_s = timeout_s
        self._mission: str | None = None
        self._latest: AutonomyIntent | None = None
        self._reason: str | None = None
        self._started_at: float | None = None

    @property
    def active(self) -> bool:
        return self._mission is not None

    @property
    def mission(self) -> str | None:
        return self._mission

    def start(self, mission: str | None = None, now: float | None = None) -> str:
        if self.active:
            raise RuntimeError("autonomy is already active")
        self._mission = _mission(mission or "mission-" + secrets.token_hex(6))
        self._latest = None
        self._reason = None
        self._started_at = time.monotonic() if now is None else now
        return self._mission

    def receive(self, intent: AutonomyIntent) -> bool:
        if intent.mission != self._mission:
            return False
        if self._latest is not None and intent.seq <= self._latest.seq:
            return False
        self._latest = intent
        return True

    def cancel(self, reason: str) -> None:
        self._mission = None
        self._latest = None
        self._reason = reason
        self._started_at = None

    def motion(self, now: float | None = None) -> DriveIntent | None:
        now = time.monotonic() if now is None else now
        if not self.active:
            return None
        if self._latest is None:
            if self._started_at is not None and now - self._started_at < self.timeout_s:
                return DriveIntent()
            self.cancel("autonomy_timeout")
            return None
        if not self._latest.is_fresh(now):
            self.cancel("autonomy_timeout")
            return None
        return self._latest.motion

    def status(self) -> AutonomyStatus:
        return AutonomyStatus(self.active, self._mission, self._reason)
