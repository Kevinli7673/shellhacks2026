"""Small, expiring goal/status datagrams for the Gazebo dashboard only."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import secrets
import time

from .bridge_ipc import DatagramReceiver, DatagramSender


GOAL_TIMEOUT_S = 0.25
STATUS_TIMEOUT_S = 0.5
GOAL_STATES = {"idle", "pending", "executing", "succeeded", "canceled", "aborted", "failed", "rejected"}


def navigation_socket(name: str) -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/rescuebot-{os.getuid()}")) / name


def _number(value: object, limit: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a finite number")
    if abs(value) > limit or not math.isfinite(value):
        raise ValueError("number outside bounds")
    return float(value)


def _identifier(value: object) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("invalid identifier")


def goal_offsets(forward: object, right: object) -> tuple[float, float]:
    forward, right = _number(forward, 2.0), _number(right, 2.0)
    if not 0.1 <= math.hypot(forward, right) <= 2.0:
        raise ValueError("choose a goal 0.1 to 2 metres away")
    return forward, right


def encode_navigation(record: dict) -> bytes:
    raw = json.dumps(record, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 1024:
        raise ValueError("navigation datagram too large")
    return raw


def _decode(raw: bytes, now: float, lifetime: float) -> dict:
    if len(raw) > 1024:
        raise ValueError("navigation datagram too large")
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError("expected an object")
    expiry = _number(record.get("expires_at"), 1e12)
    if not now < expiry <= now + lifetime:
        raise ValueError("expired or implausible deadline")
    return record


def decode_navigation_goal(raw: bytes, now: float) -> dict:
    record = _decode(raw, now, GOAL_TIMEOUT_S)
    if set(record) != {"mission", "request_id", "expires_at", "forward", "right"}:
        raise ValueError("invalid goal fields")
    _identifier(record["mission"])
    _identifier(record["request_id"])
    goal_offsets(record["forward"], record["right"])
    return record


def decode_navigation_status(raw: bytes, now: float) -> dict:
    record = _decode(raw, now, STATUS_TIMEOUT_S)
    if set(record) != {"ready", "reason", "mission", "active", "goal_state", "request_id", "pose", "expires_at"}:
        raise ValueError("invalid status fields")
    if not isinstance(record["ready"], bool) or not isinstance(record["active"], bool):
        raise ValueError("invalid navigation flags")
    if record["goal_state"] not in GOAL_STATES:
        raise ValueError("invalid goal state")
    if not isinstance(record["reason"], str) or len(record["reason"]) > 100:
        raise ValueError("invalid readiness reason")
    for key in ("mission", "request_id"):
        if record[key] is not None:
            _identifier(record[key])
    pose = record["pose"]
    if pose is not None:
        if not isinstance(pose, dict) or set(pose) != {"x", "y", "yaw"}:
            raise ValueError("invalid map pose")
        for value in pose.values():
            _number(value, 1e6)
    if record["ready"] and pose is None:
        raise ValueError("ready navigation needs a pose")
    return record


class NavigationHostEndpoint:
    """No ROS or serial dependencies; the control loop never waits for a goal."""

    def __init__(self, goal_socket: str | Path, status_socket: str | Path) -> None:
        self._sender = DatagramSender(goal_socket)
        self._receiver = DatagramReceiver(status_socket)
        self._latest: dict | None = None
        self._pending: tuple[str, str, float] | None = None

    def state(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        for raw in self._receiver.drain():
            try:
                record = decode_navigation_status(raw, now)
                if self._latest is None or record["expires_at"] > self._latest["expires_at"]:
                    self._latest = record
            except (ValueError, TypeError, KeyError):
                continue
        if self._latest is None or now >= self._latest["expires_at"]:
            return {"ready": False, "reason": "Waiting for navigation", "goal_state": "idle", "pose": None, "pending": False}
        result = {**self._latest, "pending": False}
        if self._pending:
            mission, request_id, deadline = self._pending
            if result["mission"] != mission or not result["active"] or result["request_id"] == request_id:
                self._pending = None
            elif now < deadline:
                result["pending"] = True
            else:
                result["request_error"] = "Goal was not acknowledged. Send it again."
        return result

    def send_goal(self, mission: str, forward: object, right: object) -> bool:
        forward, right = goal_offsets(forward, right)
        now = time.monotonic()
        status = self.state(now)
        if (not status["ready"] or not status.get("active") or status.get("mission") != mission
                or status["pending"] or status["goal_state"] in {"pending", "executing"}):
            return False
        request_id = secrets.token_hex(12)
        sent = self._sender.send(encode_navigation({
            "mission": mission, "request_id": request_id,
            "expires_at": now + GOAL_TIMEOUT_S, "forward": forward, "right": right,
        }))
        if sent:
            self._pending = (mission, request_id, now + STATUS_TIMEOUT_S)
        return sent

    def close(self) -> None:
        self._sender.close()
        self._receiver.close()
