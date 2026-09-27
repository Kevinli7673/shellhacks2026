"""Nonblocking Unix-datagram IPC between the command arbiter and the motor bridge.

The arbiter sends its full current intent every control tick: "drive" while
armed or waiting for the arm confirmation (zero axes while waiting), "stop"
otherwise, plus a one-shot "arm" on explicit Enable Driving. Sending "stop"
while an arm is pending cancels that arm. A one-shot "accessories" carries
the requested buzzer/light state and never affects arming or driving.
Resending "stop" every tick means a dropped datagram is repaired on the next
tick, and the bridge's freshness deadline covers anything longer.

Every command carries the arbiter's identity (a random id per control-service
run), an increasing seq, and an absolute time.monotonic() expiry. Monotonic
time is system-wide, so both processes on one machine share the clock.
Senders never block: a full or missing receiver drops the datagram and counts
it. Receivers drain without blocking and keep only the newest drive command.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
import errno
import json
import math
import os
from pathlib import Path
import socket
from typing import Any

from .serial_protocol import MAX_PWM


COMMAND_KINDS = ("drive", "stop", "arm", "accessories")
MAX_DATAGRAM_BYTES = 1024
_ID_MAX = 64
_REASON_MAX = 32


class IpcError(ValueError):
    pass


def _finite(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IpcError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise IpcError(f"{name} out of range")
    return value


def _short_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= limit:
        raise IpcError(f"{name} must be a short non-empty string")
    return value


@dataclass(frozen=True)
class ArbiterCommand:
    kind: str
    arbiter: str
    seq: int
    expires_at: float
    forward: float = 0.0
    sideways: float = 0.0
    turn: float = 0.0
    speed_limit: int = 0
    buzzer: bool = False
    light: bool = False

    def __post_init__(self) -> None:
        if self.kind not in COMMAND_KINDS:
            raise IpcError(f"unknown kind {self.kind!r}")
        if not isinstance(self.buzzer, bool) or not isinstance(self.light, bool):
            raise IpcError("buzzer and light must be booleans")
        _short_text(self.arbiter, "arbiter", _ID_MAX)
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq <= 0:
            raise IpcError("seq must be a positive integer")
        object.__setattr__(self, "expires_at", _finite(self.expires_at, "expires_at", 0.0, math.inf))
        for name in ("forward", "sideways", "turn"):
            object.__setattr__(self, name, _finite(getattr(self, name), name, -1.0, 1.0))
        if (
            isinstance(self.speed_limit, bool)
            or not isinstance(self.speed_limit, int)
            or not 0 <= self.speed_limit <= MAX_PWM
        ):
            raise IpcError("speed_limit must be an integer between 0 and 255")

    def is_fresh(self, now: float) -> bool:
        return now < self.expires_at

    def encode(self) -> bytes:
        return json.dumps(
            {
                "kind": self.kind,
                "arbiter": self.arbiter,
                "seq": self.seq,
                "expires_at": self.expires_at,
                "forward": self.forward,
                "sideways": self.sideways,
                "turn": self.turn,
                "speed_limit": self.speed_limit,
                "buzzer": self.buzzer,
                "light": self.light,
            },
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def decode(cls, data: bytes) -> "ArbiterCommand":
        record = _decode_object(data)
        try:
            return cls(**record)
        except TypeError as exc:
            raise IpcError(f"bad command fields: {exc}") from exc


@dataclass(frozen=True)
class BridgeStatus:
    """What the bridge reports back to the control service each loop."""

    sent_at: float
    transport_connected: bool
    armed: bool
    arm_pending: bool
    fault: str | None
    ack_age_ms: int | None
    wheels: dict[str, int] = field(default_factory=lambda: {"fl": 0, "fr": 0, "rl": 0, "rr": 0})
    arbiter: str | None = None
    rejected_commands: int = 0
    imu: dict[str, Any] | None = None
    accessories: dict[str, bool] = field(default_factory=lambda: {"buzzer": False, "light": False})
    # The QT Py's on-die temperature in °C; None until firmware reports one.
    mcu_temp_c: float | None = None

    def encode(self) -> bytes:
        return json.dumps(self.__dict__, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @classmethod
    def decode(cls, data: bytes) -> "BridgeStatus":
        record = _decode_object(data)
        try:
            status = cls(**record)
        except TypeError as exc:
            raise IpcError(f"bad status fields: {exc}") from exc
        _finite(status.sent_at, "sent_at", 0.0, math.inf)
        if not isinstance(status.armed, bool) or not isinstance(status.transport_connected, bool):
            raise IpcError("bad status flags")
        if status.fault is not None:
            _short_text(status.fault, "fault", _REASON_MAX)
        accessories = status.accessories
        if (
            not isinstance(accessories, dict)
            or set(accessories) != {"buzzer", "light"}
            or not all(isinstance(value, bool) for value in accessories.values())
        ):
            raise IpcError("bad accessories")
        if status.mcu_temp_c is not None:
            _finite(status.mcu_temp_c, "mcu_temp_c", -40.0, 150.0)
        return status


def _decode_object(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_DATAGRAM_BYTES:
        raise IpcError("datagram too large")
    try:
        record = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IpcError(f"invalid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise IpcError("datagram must be a JSON object")
    return record


def _reject_constant(token: str) -> Any:
    raise IpcError(f"non-finite value {token}")


class DatagramSender:
    """Fire-and-forget sender; never blocks and never raises on a missing peer."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self.sent_count = 0
        self.dropped_count = 0

    def send(self, payload: bytes) -> bool:
        if len(payload) > MAX_DATAGRAM_BYTES:
            raise IpcError("datagram too large")
        try:
            self._socket.sendto(payload, self.path)
        except (BlockingIOError, FileNotFoundError, ConnectionRefusedError):
            self.dropped_count += 1
            return False
        except OSError as exc:
            if exc.errno in (errno.ENOBUFS, errno.EAGAIN, errno.ENOENT, errno.ECONNREFUSED):
                self.dropped_count += 1
                return False
            raise
        self.sent_count += 1
        return True

    def close(self) -> None:
        self._socket.close()


class DatagramReceiver:
    """Bound, nonblocking receiver that drains a bounded number of datagrams."""

    def __init__(self, path: str | Path, max_drain: int = 64) -> None:
        self.path = str(path)
        self.max_drain = max_drain
        with suppress(FileNotFoundError):
            os.unlink(self.path)  # stale socket file from a previous run
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._socket.bind(self.path)
        self._socket.setblocking(False)

    def fileno(self) -> int:
        return self._socket.fileno()

    def drain(self) -> list[bytes]:
        items: list[bytes] = []
        for _ in range(self.max_drain):
            try:
                items.append(self._socket.recv(MAX_DATAGRAM_BYTES + 1))
            except BlockingIOError:
                break
        return items

    def close(self) -> None:
        self._socket.close()
        with suppress(FileNotFoundError):
            os.unlink(self.path)


@dataclass
class CommandBatch:
    """One drain, reduced: controls in order, and only the newest drive."""

    controls: list[ArbiterCommand]
    latest_drive: ArbiterCommand | None
    rejected: int


def reduce_commands(datagrams: list[bytes]) -> CommandBatch:
    controls: list[ArbiterCommand] = []
    latest_drive: ArbiterCommand | None = None
    rejected = 0
    for data in datagrams:
        try:
            command = ArbiterCommand.decode(data)
        except IpcError:
            rejected += 1
            continue
        if command.kind == "drive":
            latest_drive = command
        else:
            controls.append(command)
            if command.kind == "stop":
                # A stop supersedes any drive that arrived before it.
                latest_drive = None
    return CommandBatch(controls, latest_drive, rejected)
