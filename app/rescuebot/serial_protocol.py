"""Newline-delimited JSON messages between the Pi motor bridge and the ESP32-S2.

The drive packet and drive acknowledgment are the frozen shared interface.
The arm/disarm commands and the arm_ack, disarm_ack, fault, and imu replies
follow the ESP32 workstream's firmware (feature/esp32-controller, 48ae9dd,
firmware/include/protocol_messages.h). They are agreed in practice but not
yet recorded as frozen in changes.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import secrets
from typing import Any, Mapping


BAUD_RATE = 115200
MAX_LINE_BYTES = 200  # firmware LineReader kMaxLineLength
MAX_PWM = 255
SEQ_MAX = 2**31 - 1
_SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")

DRIVE_FIELDS = frozenset({"type", "session", "seq", "forward", "sideways", "turn", "speed_limit"})
CONTROL_FIELDS = frozenset({"type", "session", "seq"})
ACK_FIELDS = frozenset({"session", "ack", "fl", "fr", "rl", "rr"})


class ProtocolError(ValueError):
    """A line that must not drive motors or refresh any watchdog."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def new_session_id() -> str:
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Field validation shared by both directions.


def _require_session(value: Any) -> str:
    if not isinstance(value, str) or not _SESSION_PATTERN.fullmatch(value):
        raise ProtocolError("bad_session", repr(value))
    return value


def _require_seq(value: Any, name: str = "seq") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= SEQ_MAX:
        raise ProtocolError("bad_seq", f"{name}={value!r}")
    return value


def _require_axis(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError("bad_axis", f"{name}={value!r}")
    value = float(value)
    if not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ProtocolError("bad_axis", f"{name}={value!r}")
    return value


def _require_pwm(name: str, value: Any, signed: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError("bad_pwm", f"{name}={value!r}")
    low = -MAX_PWM if signed else 0
    if not low <= value <= MAX_PWM:
        raise ProtocolError("bad_pwm", f"{name}={value!r}")
    return value


def _require_fields(data: Mapping[str, Any], expected: frozenset[str]) -> None:
    keys = set(data)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ProtocolError("bad_fields", f"missing={missing} extra={extra}")


def _reject_constant(token: str) -> Any:
    raise ProtocolError("non_finite", token)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_key", key)
        result[key] = value
    return result


def decode_object(line: bytes | str) -> dict[str, Any]:
    """Strictly decode one line (without its newline) into a JSON object."""
    raw = line.encode("utf-8") if isinstance(line, str) else line
    if len(raw) > MAX_LINE_BYTES:
        raise ProtocolError("oversized", f"{len(raw)} bytes")
    try:
        text = raw.decode("utf-8")
        data = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except UnicodeDecodeError as exc:
        raise ProtocolError("bad_encoding", str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ProtocolError("bad_json", exc.msg) from exc
    if not isinstance(data, dict):
        raise ProtocolError("not_object")
    return data


def _encode(data: Mapping[str, Any]) -> bytes:
    line = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(line) > MAX_LINE_BYTES:
        raise ProtocolError("oversized", f"{len(line)} bytes")
    return line + b"\n"


# ---------------------------------------------------------------------------
# Pi -> ESP32 commands.


@dataclass(frozen=True)
class DriveCommand:
    session: str
    seq: int
    forward: float
    sideways: float
    turn: float
    speed_limit: int

    def __post_init__(self) -> None:
        _require_session(self.session)
        _require_seq(self.seq)
        for name in ("forward", "sideways", "turn"):
            object.__setattr__(self, name, _require_axis(name, getattr(self, name)))
        _require_pwm("speed_limit", self.speed_limit, signed=False)

    def encode(self) -> bytes:
        return _encode(
            {
                "type": "drive",
                "session": self.session,
                "seq": self.seq,
                "forward": round(self.forward, 4),
                "sideways": round(self.sideways, 4),
                "turn": round(self.turn, 4),
                "speed_limit": self.speed_limit,
            }
        )


@dataclass(frozen=True)
class ControlCommand:
    """Explicit arm or disarm request (proposed interface)."""

    type: str
    session: str
    seq: int

    def __post_init__(self) -> None:
        if self.type not in ("arm", "disarm"):
            raise ProtocolError("bad_type", repr(self.type))
        _require_session(self.session)
        _require_seq(self.seq)

    def encode(self) -> bytes:
        return _encode({"type": self.type, "session": self.session, "seq": self.seq})


def parse_command(line: bytes | str) -> DriveCommand | ControlCommand:
    """Parse a Pi -> ESP32 line exactly as the firmware must."""
    data = decode_object(line)
    kind = data.get("type")
    if kind == "drive":
        _require_fields(data, DRIVE_FIELDS)
        return DriveCommand(
            session=data["session"],
            seq=data["seq"],
            forward=data["forward"],
            sideways=data["sideways"],
            turn=data["turn"],
            speed_limit=data["speed_limit"],
        )
    if kind in ("arm", "disarm"):
        _require_fields(data, CONTROL_FIELDS)
        return ControlCommand(type=kind, session=data["session"], seq=data["seq"])
    raise ProtocolError("bad_type", repr(kind))


# ---------------------------------------------------------------------------
# ESP32 -> Pi messages.


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError("bad_field", f"{name}={value!r}")
    return value


def _require_reason(value: Any) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 32:
        raise ProtocolError("bad_field", f"reason={value!r}")
    return value


@dataclass(frozen=True)
class DriveAck:
    session: str
    ack: int
    fl: int
    fr: int
    rl: int
    rr: int

    def __post_init__(self) -> None:
        _require_session(self.session)
        _require_seq(self.ack, "ack")
        for name in ("fl", "fr", "rl", "rr"):
            _require_pwm(name, getattr(self, name), signed=True)

    def wheels(self) -> dict[str, int]:
        return {"fl": self.fl, "fr": self.fr, "rl": self.rl, "rr": self.rr}

    def encode(self) -> bytes:
        return _encode({"session": self.session, "ack": self.ack, **self.wheels()})


@dataclass(frozen=True)
class ControlAck:
    """Firmware reply to arm ("arm_ack") or disarm ("disarm_ack").

    It echoes the command's session and seq, even when that session is not
    the firmware's current one.
    """

    type: str
    session: str
    seq: int
    armed: bool

    def __post_init__(self) -> None:
        if self.type not in ("arm_ack", "disarm_ack"):
            raise ProtocolError("bad_type", repr(self.type))
        _require_session(self.session)
        _require_seq(self.seq)
        _require_bool("armed", self.armed)

    def encode(self) -> bytes:
        return _encode(
            {"type": self.type, "session": self.session, "seq": self.seq, "armed": self.armed}
        )


@dataclass(frozen=True)
class FirmwareFault:
    """Unprompted disarm report: malformed_packet, oversized_packet, or watchdog_expired.

    Faults carry no session; the firmware is disarmed whenever it sends one.
    """

    reason: str
    armed: bool = False

    def __post_init__(self) -> None:
        _require_reason(self.reason)
        _require_bool("armed", self.armed)

    def encode(self) -> bytes:
        return _encode({"type": "fault", "reason": self.reason, "armed": self.armed})


@dataclass(frozen=True)
class ImuTelemetry:
    """BNO055 telemetry at ~20 Hz. Never counts as a motor acknowledgment."""

    timestamp_ms: int
    available: bool
    heading: float | None = None
    calibration: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ms, bool) or not isinstance(self.timestamp_ms, int) or self.timestamp_ms < 0:
            raise ProtocolError("bad_field", f"timestamp_ms={self.timestamp_ms!r}")
        _require_bool("available", self.available)
        if self.heading is not None:
            if isinstance(self.heading, bool) or not isinstance(self.heading, (int, float)) or not math.isfinite(self.heading):
                raise ProtocolError("bad_field", f"heading={self.heading!r}")
        if self.calibration is not None:
            if isinstance(self.calibration, bool) or self.calibration not in (0, 1, 2, 3):
                raise ProtocolError("bad_field", f"calibration={self.calibration!r}")

    def encode(self) -> bytes:
        data: dict[str, Any] = {
            "type": "imu",
            "timestamp_ms": self.timestamp_ms,
            "available": self.available,
        }
        if self.available:
            data["heading"] = self.heading
            data["calibration"] = self.calibration
        return _encode(data)


InboundMessage = DriveAck | ControlAck | FirmwareFault | ImuTelemetry


def parse_inbound(line: bytes | str) -> InboundMessage:
    """Parse an ESP32 -> Pi line."""
    data = decode_object(line)
    kind = data.get("type")
    if kind is None:
        _require_fields(data, ACK_FIELDS)
        return DriveAck(**data)
    if kind in ("arm_ack", "disarm_ack"):
        _require_fields(data, frozenset({"type", "session", "seq", "armed"}))
        return ControlAck(kind, data["session"], data["seq"], data["armed"])
    if kind == "fault":
        _require_fields(data, frozenset({"type", "reason", "armed"}))
        return FirmwareFault(data["reason"], data["armed"])
    if kind == "imu":
        keys = set(data)
        if not {"type", "timestamp_ms", "available"} <= keys <= {
            "type", "timestamp_ms", "available", "heading", "calibration"
        }:
            raise ProtocolError("bad_fields", f"imu keys={sorted(keys)}")
        return ImuTelemetry(
            data["timestamp_ms"], data["available"], data.get("heading"), data.get("calibration")
        )
    raise ProtocolError("bad_type", repr(kind))


# ---------------------------------------------------------------------------
# Framing.


class LineDecoder:
    """Bounded reassembly of newline-delimited lines from arbitrary chunks.

    A line longer than MAX_LINE_BYTES is discarded through its newline and
    counted, so a missing newline cannot grow the buffer without bound.
    """

    def __init__(self, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self.max_line_bytes = max_line_bytes
        self._buffer = bytearray()
        self._discarding = False
        self.oversized_count = 0

    def feed(self, chunk: bytes) -> list[bytes]:
        lines: list[bytes] = []
        parts = chunk.split(b"\n")
        for index, part in enumerate(parts):
            self._append(part)
            if index == len(parts) - 1:
                break  # unterminated remainder stays buffered
            line = bytes(self._buffer).rstrip(b"\r")
            if line and not self._discarding:
                lines.append(line)
            self._buffer.clear()
            self._discarding = False
        return lines

    def _append(self, data: bytes) -> None:
        if self._discarding:
            return
        if len(self._buffer) + len(data) > self.max_line_bytes + 1:  # allow "\r"
            self._buffer.clear()
            self._discarding = True
            self.oversized_count += 1
            return
        self._buffer.extend(data)
