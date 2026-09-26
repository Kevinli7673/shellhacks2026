"""Pi-side session, sequencing, arming, and ACK-freshness logic for the ESP32 link.

This is transport-agnostic: methods return encoded lines to write and accept
raw bytes read from the port. It never retransmits old movement packets and
never arms on connect; arming requires request_arm() and a matching firmware
state report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .serial_protocol import (
    ControlCommand,
    DriveAck,
    DriveCommand,
    FirmwareState,
    ImuTelemetry,
    LineDecoder,
    ProtocolError,
    SEQ_MAX,
    new_session_id,
    parse_inbound,
)


ACK_TIMEOUT_S = 0.250


@dataclass(frozen=True)
class LinkSnapshot:
    connected: bool
    session: str | None
    armed: bool
    arm_pending: bool
    fault: str | None
    last_sent_seq: int
    last_ack_seq: int
    ack_age_ms: int | None
    wheels: dict[str, int]
    rejected_lines: int
    stale_acks: int
    imu_messages: int

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class MotorLink:
    def __init__(
        self,
        ack_timeout_s: float = ACK_TIMEOUT_S,
        session_factory: Callable[[], str] = new_session_id,
    ) -> None:
        if ack_timeout_s <= 0:
            raise ValueError("ack_timeout_s must be positive")
        self.ack_timeout_s = ack_timeout_s
        self._session_factory = session_factory
        self._decoder = LineDecoder()
        self.connected = False
        self.session: str | None = None
        self.armed = False
        self.fault: str | None = None
        self._arm_seq: int | None = None
        self._next_seq = 1
        self.last_sent_seq = 0
        self.last_ack_seq = 0
        self._ack_deadline_from: float | None = None
        self.wheels = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}
        self.latest_imu: ImuTelemetry | None = None
        self.rejected_lines = 0
        self.stale_acks = 0
        self.imu_messages = 0

    # -- connection lifecycle ------------------------------------------------

    def connect(self) -> bytes:
        """Start a fresh session, disarmed, and tell the firmware about it."""
        self._reset_session(self._session_factory())
        self.connected = True
        self.fault = None
        return self._control("disarm")

    def disconnect(self) -> None:
        self.connected = False
        self._disarm_locally("link_lost")
        self.session = None

    def _reset_session(self, session: str) -> None:
        self.session = session
        self._decoder = LineDecoder()
        self.armed = False
        self._arm_seq = None
        self._next_seq = 1
        self.last_sent_seq = 0
        self.last_ack_seq = 0
        self._ack_deadline_from = None
        self.wheels = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}

    def _take_seq(self) -> int:
        if self._next_seq > SEQ_MAX:
            # Exhausting a session is not expected in practice; fail safe.
            self._disarm_locally("seq_exhausted")
            raise ProtocolError("seq_exhausted")
        seq = self._next_seq
        self._next_seq += 1
        self.last_sent_seq = seq
        return seq

    def _control(self, kind: str) -> bytes:
        assert self.session is not None
        return ControlCommand(kind, self.session, self._take_seq()).encode()

    def _disarm_locally(self, reason: str | None) -> None:
        self.armed = False
        self._arm_seq = None
        self._ack_deadline_from = None
        self.wheels = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}
        if reason is not None:
            self.fault = reason

    # -- outbound ----------------------------------------------------------

    def request_arm(self) -> bytes | None:
        """Explicit Enable Driving. Armed only after the firmware confirms."""
        if not self.connected:
            return None
        self.fault = None
        self.armed = False
        line = self._control("arm")
        self._arm_seq = self.last_sent_seq
        return line

    def disarm(self, reason: str | None = None) -> bytes | None:
        """Stop: disarm immediately, independent of the drive cadence."""
        self._disarm_locally(reason)
        if not self.connected:
            return None
        return self._control("disarm")

    def drive(
        self,
        forward: float,
        sideways: float,
        turn: float,
        speed_limit: int,
        now: float,
    ) -> bytes | None:
        """Encode a freshly computed command; returns a disarm line on ACK timeout."""
        timeout = self.check(now)
        if timeout is not None or not (self.connected and self.armed):
            return timeout
        assert self.session is not None
        return DriveCommand(
            self.session, self._take_seq(), forward, sideways, turn, speed_limit
        ).encode()

    def check(self, now: float) -> bytes | None:
        """Disarm if armed without an advancing ACK inside the deadline."""
        if self.armed and self._ack_deadline_from is not None:
            if now - self._ack_deadline_from > self.ack_timeout_s:
                return self.disarm("ack_timeout")
        return None

    # -- inbound -----------------------------------------------------------

    def receive(self, chunk: bytes, now: float) -> list[bytes]:
        """Consume port bytes; returns lines to send back (safety disarms only)."""
        replies: list[bytes] = []
        oversized_before = self._decoder.oversized_count
        lines = self._decoder.feed(chunk)
        self.rejected_lines += self._decoder.oversized_count - oversized_before
        for line in lines:
            try:
                message = parse_inbound(line)
            except ProtocolError:
                self.rejected_lines += 1
                continue
            if isinstance(message, DriveAck):
                self._on_ack(message, now)
            elif isinstance(message, FirmwareState):
                reply = self._on_state(message, now)
                if reply is not None:
                    replies.append(reply)
            else:
                self.latest_imu = message
                self.imu_messages += 1
        return replies

    def _on_ack(self, ack: DriveAck, now: float) -> None:
        if not self.connected or ack.session != self.session:
            self.stale_acks += 1
            return
        if ack.ack <= self.last_ack_seq or ack.ack > self.last_sent_seq:
            self.stale_acks += 1
            return
        self.last_ack_seq = ack.ack
        if self.armed:
            self._ack_deadline_from = now
            self.wheels = ack.wheels()

    def _on_state(self, state: FirmwareState, now: float) -> bytes | None:
        if not self.connected:
            return None
        if state.session != self.session:
            # Firmware rebooted or holds another session: never keep driving.
            # Re-announce our session with a disarm so it cannot stay armed.
            if self.armed or self._arm_seq is not None:
                return self.disarm("session_mismatch")
            return self._control("disarm") if state.armed else None
        if state.ack is not None:
            if state.ack <= self.last_ack_seq or state.ack > self.last_sent_seq:
                self.stale_acks += 1
                return None
            self.last_ack_seq = state.ack
        if not state.armed:
            if self.armed or self._arm_seq is not None:
                self._disarm_locally(state.fault or "firmware_disarmed")
            return None
        if self._arm_seq is not None and state.ack == self._arm_seq:
            self.armed = True
            self._arm_seq = None
            self._ack_deadline_from = now
            self.fault = None
            return None
        if not self.armed:
            # An unrequested armed report must not arm the Pi side.
            return self.disarm("unexpected_arm")
        return None

    def snapshot(self, now: float) -> LinkSnapshot:
        ack_age = None
        if self._ack_deadline_from is not None:
            ack_age = round(max(0.0, now - self._ack_deadline_from) * 1000)
        return LinkSnapshot(
            connected=self.connected,
            session=self.session,
            armed=self.armed,
            arm_pending=self._arm_seq is not None,
            fault=self.fault,
            last_sent_seq=self.last_sent_seq,
            last_ack_seq=self.last_ack_seq,
            ack_age_ms=ack_age,
            wheels=dict(self.wheels),
            rejected_lines=self.rejected_lines,
            stale_acks=self.stale_acks,
            imu_messages=self.imu_messages,
        )
