"""Bounded host/ROS IPC for the simulation autonomy source.

The command socket carries only normalized inputs into ``RobotControlService``.
The status socket lets ROS observe start/cancel state.  Neither socket is a
serial transport or a route to the motor shield.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import time
from typing import Any

from .autonomy import AutonomyIntent, AutonomyStatus
from .bridge_ipc import DatagramReceiver, DatagramSender, IpcError


MAX_AUTONOMY_BYTES = 512


class AutonomyIpcError(ValueError):
    pass


def _decode(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_AUTONOMY_BYTES:
        raise AutonomyIpcError("datagram too large")
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise AutonomyIpcError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AutonomyIpcError("datagram must be an object")
    return data


def encode_autonomy_intent(intent: AutonomyIntent) -> bytes:
    data = {
        "type": "autonomy_intent",
        "mission": intent.mission,
        "seq": intent.seq,
        "expires_at": intent.expires_at,
        "forward": intent.forward,
        "sideways": intent.sideways,
        "turn": intent.turn,
    }
    return json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode_autonomy_intent(raw: bytes) -> AutonomyIntent:
    data = _decode(raw)
    expected = {"type", "mission", "seq", "expires_at", "forward", "sideways", "turn"}
    if set(data) != expected or data.get("type") != "autonomy_intent":
        raise AutonomyIpcError("invalid autonomy intent fields")
    try:
        return AutonomyIntent(
            mission=data["mission"],
            seq=data["seq"],
            expires_at=data["expires_at"],
            forward=data["forward"],
            sideways=data["sideways"],
            turn=data["turn"],
        )
    except ValueError as exc:
        raise AutonomyIpcError(str(exc)) from exc


def encode_autonomy_status(status: AutonomyStatus) -> bytes:
    data = {"type": "autonomy_status", "active": status.active, "mission": status.mission, "reason": status.reason}
    return json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode_autonomy_status(raw: bytes) -> AutonomyStatus:
    data = _decode(raw)
    expected = {"type", "active", "mission", "reason"}
    if set(data) != expected or data.get("type") != "autonomy_status" or not isinstance(data["active"], bool):
        raise AutonomyIpcError("invalid autonomy status fields")
    mission, reason = data["mission"], data["reason"]
    if mission is not None:
        try:
            if not isinstance(mission, str):
                raise ValueError
            mission = AutonomyIntent(mission, 1, 0.0, 0.0, 0.0, 0.0).mission
        except ValueError as exc:
            raise AutonomyIpcError("invalid autonomy mission") from exc
    if reason is not None and (not isinstance(reason, str) or len(reason) > 64):
        raise AutonomyIpcError("invalid autonomy reason")
    return AutonomyStatus(data["active"], mission, reason)


class AutonomyHostEndpoint:
    """Host-side command receiver and best-effort status publisher."""

    def __init__(self, command_socket: str | Path, status_socket: str | Path) -> None:
        self._receiver = DatagramReceiver(command_socket)
        self._sender = DatagramSender(status_socket)
        self.rejected_commands = 0

    def drain(self) -> list[AutonomyIntent]:
        intents: list[AutonomyIntent] = []
        for raw in self._receiver.drain():
            try:
                intents.append(decode_autonomy_intent(raw))
            except AutonomyIpcError:
                self.rejected_commands += 1
        return intents

    def publish(self, status: AutonomyStatus) -> bool:
        return self._sender.send(encode_autonomy_status(status))

    def close(self) -> None:
        self._sender.close()
        self._receiver.close()


def fresh_expiry(now: float | None = None, timeout_s: float = 0.250) -> float:
    if timeout_s <= 0.0 or not math.isfinite(timeout_s):
        raise ValueError("timeout_s must be finite and positive")
    return (time.monotonic() if now is None else now) + timeout_s
