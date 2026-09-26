"""Control-service side of the motor bridge: the command arbiter's sender.

Every call to send() transmits the arbiter's full current intent: "stop" while
disabled, a zero "drive" while waiting for the firmware's arm_ack, and the
requested "drive" once armed. Resending every control tick repairs dropped
datagrams. Status datagrams from the bridge are drained without blocking.
"""

from __future__ import annotations

from pathlib import Path
import secrets
import time

from .bridge_ipc import ArbiterCommand, BridgeStatus, DatagramReceiver, DatagramSender, IpcError
from .control import ControlSnapshot


COMMAND_TTL_S = 0.250
STATUS_STALE_S = 0.500


class BridgeMotorBackend:
    requires_arm_confirmation = True

    def __init__(
        self,
        command_socket: str | Path,
        status_socket: str | Path,
        arbiter_id: str | None = None,
        status_stale_s: float = STATUS_STALE_S,
    ) -> None:
        self.arbiter = arbiter_id or "svc-" + secrets.token_hex(6)
        self.status_stale_s = status_stale_s
        self._sender = DatagramSender(command_socket)
        self._receiver = DatagramReceiver(status_socket)
        self._seq = 0
        self.status: BridgeStatus | None = None
        self._status_received_at: float | None = None
        self.rejected_status = 0
        self.arming = False
        self.last_kind: str | None = None

    # -- status ------------------------------------------------------------

    def poll(self, now: float) -> BridgeStatus | None:
        for data in self._receiver.drain():
            try:
                status = BridgeStatus.decode(data)
            except IpcError:
                self.rejected_status += 1
                continue
            self.status = status
            self._status_received_at = now
        return self.status

    def status_age(self, now: float) -> float | None:
        if self._status_received_at is None:
            return None
        return max(0.0, now - self._status_received_at)

    def healthy(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        age = self.status_age(now)
        return (
            self.status is not None
            and age is not None
            and age <= self.status_stale_s
            and self.status.transport_connected
        )

    @property
    def firmware_armed(self) -> bool:
        return self.status is not None and self.status.armed

    @property
    def fault(self) -> str | None:
        return None if self.status is None else self.status.fault

    # -- commands ------------------------------------------------------------

    def _send(self, kind: str, now: float, snapshot: ControlSnapshot | None = None) -> bool:
        self._seq += 1
        forward = sideways = turn = 0.0
        speed_limit = 0
        if kind == "drive" and snapshot is not None:
            speed_limit = snapshot.speed_limit
            if not self.arming:
                forward = snapshot.intent.forward
                sideways = snapshot.intent.sideways
                turn = snapshot.intent.turn
        command = ArbiterCommand(
            kind=kind,
            arbiter=self.arbiter,
            seq=self._seq,
            expires_at=now + COMMAND_TTL_S,
            forward=forward,
            sideways=sideways,
            turn=turn,
            speed_limit=speed_limit,
        )
        self.last_kind = kind
        return self._sender.send(command.encode())

    def request_arm(self, now: float) -> bool:
        self.arming = True
        return self._send("arm", now)

    def send(self, snapshot: ControlSnapshot, now: float) -> bool:
        if not snapshot.armed:
            self.arming = False
            return self._send("stop", now)
        return self._send("drive", now, snapshot)

    def close(self) -> None:
        self._send("stop", time.monotonic())
        self._sender.close()
        self._receiver.close()

    # -- dashboard -----------------------------------------------------------

    @property
    def wheels(self) -> dict[str, int]:
        if self.status is None or not self.status.armed:
            return {"fl": 0, "fr": 0, "rl": 0, "rr": 0}
        return dict(self.status.wheels)

    def as_dict(self, now: float | None = None) -> dict[str, object]:
        now = time.monotonic() if now is None else now
        age = self.status_age(now)
        return {
            "backend": "bridge",
            "healthy": self.healthy(now),
            "arming": self.arming,
            "firmware_armed": self.firmware_armed,
            "wheels": self.wheels,
            "last_reason": self.fault or self.last_kind or "no_status",
            "fault": self.fault,
            "ack_age_ms": None if self.status is None else self.status.ack_age_ms,
            "status_age_ms": None if age is None else round(age * 1000),
            "transport_connected": bool(self.status and self.status.transport_connected),
            "imu": None if self.status is None else self.status.imu,
            "dropped_commands": self._sender.dropped_count,
        }
