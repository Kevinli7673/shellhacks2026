"""Pure mecanum mixing shared by the mock backend and firmware fixtures."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class WheelOutputs:
    """Signed, pre-mapping PWM outputs in front-left, front-right, rear-left, rear-right order."""

    front_left: int
    front_right: int
    rear_left: int
    rear_right: int

    @classmethod
    def stopped(cls) -> "WheelOutputs":
        return cls(0, 0, 0, 0)

    def as_dict(self) -> dict[str, int]:
        return {
            "fl": self.front_left,
            "fr": self.front_right,
            "rl": self.rear_left,
            "rr": self.rear_right,
        }


def _require_axis(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError(f"{name} must be finite and between -1 and 1")
    return value


def _require_speed_limit(speed_limit: int) -> int:
    if not isinstance(speed_limit, int) or isinstance(speed_limit, bool):
        raise ValueError("speed_limit must be an integer")
    if not 0 <= speed_limit <= 255:
        raise ValueError("speed_limit must be between 0 and 255")
    return speed_limit


def _round_half_away_from_zero(value: float) -> int:
    """Match C++ lround for the firmware parity fixtures."""

    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def mix_mecanum(
    forward: float,
    sideways: float,
    turn: float,
    speed_limit: int,
) -> WheelOutputs:
    """Return bounded signed PWM values using the approved Rescuebot equations.

    Positive axes mean physical forward, physical right strafe, and physical
    clockwise turn. Wheel mapping and per-wheel inversions intentionally do
    not appear here; the ESP32 applies them after this calculation.
    """

    forward = _require_axis("forward", forward)
    sideways = _require_axis("sideways", sideways)
    turn = _require_axis("turn", turn)
    speed_limit = _require_speed_limit(speed_limit)

    if speed_limit == 0:
        return WheelOutputs.stopped()

    translation_magnitude = math.hypot(forward, sideways)
    if translation_magnitude > 1.0:
        forward /= translation_magnitude
        sideways /= translation_magnitude

    forward *= speed_limit
    sideways *= speed_limit
    turn *= speed_limit

    raw = (
        forward + sideways + turn,
        forward - sideways - turn,
        forward - sideways + turn,
        forward + sideways - turn,
    )
    peak = max(abs(value) for value in raw)
    if peak > speed_limit:
        scale = speed_limit / peak
        raw = tuple(value * scale for value in raw)

    return WheelOutputs(*(_round_half_away_from_zero(value) for value in raw))
