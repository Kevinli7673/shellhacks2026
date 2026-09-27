"""Bounded local IPC for the simulation-only Gazebo motor backend.

This is intentionally separate from bridge_ipc: it accepts selected logical
host commands and terminates at the Gazebo command bridge.  It has no serial,
firmware, or physical motor capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any


MAX_SIM_COMMAND_BYTES = 512


class SimulationIpcError(ValueError):
    """A malformed or unsafe simulator command."""


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimulationIpcError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise SimulationIpcError(f"{name} out of range")
    return value


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SimulationIpcError("seq must be a positive integer")
    return value


@dataclass(frozen=True)
class SimulationCommand:
    """The latest selected host command, valid only until ``expires_at``."""

    seq: int
    expires_at: float
    armed: bool
    forward: float = 0.0
    sideways: float = 0.0
    turn: float = 0.0

    def __post_init__(self) -> None:
        _sequence(self.seq)
        object.__setattr__(self, "expires_at", _number(self.expires_at, "expires_at", 0.0, math.inf))
        if not isinstance(self.armed, bool):
            raise SimulationIpcError("armed must be a boolean")
        for name in ("forward", "sideways", "turn"):
            object.__setattr__(self, name, _number(getattr(self, name), name, -1.0, 1.0))

    def is_fresh(self, now: float) -> bool:
        return now < self.expires_at

    def encode(self) -> bytes:
        data = {
            "type": "simulation_command",
            "seq": self.seq,
            "expires_at": self.expires_at,
            "armed": self.armed,
            "forward": self.forward,
            "sideways": self.sideways,
            "turn": self.turn,
        }
        encoded = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_SIM_COMMAND_BYTES:
            raise SimulationIpcError("command too large")
        return encoded

    @classmethod
    def decode(cls, raw: bytes) -> "SimulationCommand":
        if len(raw) > MAX_SIM_COMMAND_BYTES:
            raise SimulationIpcError("command too large")
        try:
            data = json.loads(raw.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise SimulationIpcError(f"invalid JSON: {exc}") from exc
        expected = {"type", "seq", "expires_at", "armed", "forward", "sideways", "turn"}
        if not isinstance(data, dict) or set(data) != expected or data.get("type") != "simulation_command":
            raise SimulationIpcError("invalid command fields")
        return cls(
            seq=data["seq"],
            expires_at=data["expires_at"],
            armed=data["armed"],
            forward=data["forward"],
            sideways=data["sideways"],
            turn=data["turn"],
        )
