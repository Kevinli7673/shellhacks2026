"""Pi-side session, sequencing, arming, and ACK-freshness logic for the ESP32 link.

This is transport-agnostic: methods return encoded lines to write and accept
raw bytes read from the port. It never retransmits old movement packets and
never arms on connect; arming requires request_arm() and a matching arm_ack.

A firmware reboot is detected from its boot fault message; if that line is
lost, from the IMU timestamp moving backwards or, at the latest, the 250 ms
ACK deadline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .serial_protocol import (
    AccessoryAck,
    AccessoryCommand,
    ControlAck,
    ControlCommand,
    DriveAck,
    DriveCommand,
    FirmwareFault,
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
    accessories: dict[str, bool]

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
        # Buzzer/light as last confirmed by the firmware; off until it says otherwise.
        self.accessories = {"buzzer": False, "light": False}
        self.rejected_lines = 0
        self.stale_acks = 0
        self.imu_messages = 0

    # -- connection lifecycle ------------------------------------------------

    def connect(self) -> bytes:
        """Start a fresh session, disarmed, and stop anything still running."""
        self._reset_session(self._session_factory())
        self.connected = True
        self.fault = None
        return self._control("disarm")

    def disconnect(self) -> None:
        self.connected = False
        self._disarm_locally("link_lost")
        self.session = None
        self.accessories = {"buzzer": False, "light": False}

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
        self.latest_imu = None

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

    def set_accessories(self, buzzer: bool, light: bool) -> bytes | None:
        """Request the buzzer/light state; accessories_ack confirms it.

        Independent of arming: it never arms, disarms, or counts as a motor ACK.
        """
        if not self.connected:
            return None
        assert self.session is not None
        return AccessoryCommand(self.session, self._take_seq(), buzzer, light).encode()

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
                self._on_drive_ack(message, now)
                reply = None
            elif isinstance(message, ControlAck):
                reply = self._on_control_ack(message, now)
            elif isinstance(message, AccessoryAck):
                self._on_accessory_ack(message)
                reply = None
            elif isinstance(message, FirmwareFault):
                reply = self._on_fault(message)
            else:
                reply = self._on_imu(message)
            if reply is not None:
                replies.append(reply)
        return replies

    def _fresh_seq(self, seq: int) -> bool:
        """True for an ACK of a seq we sent that is newer than the last ACK."""
        if seq <= self.last_ack_seq or seq > self.last_sent_seq:
            self.stale_acks += 1
            return False
        self.last_ack_seq = seq
        return True

    def _on_drive_ack(self, ack: DriveAck, now: float) -> None:
        if not self.connected or ack.session != self.session:
            self.stale_acks += 1
            return
        if self._fresh_seq(ack.ack) and self.armed:
            self._ack_deadline_from = now
            self.wheels = ack.wheels()

    def _on_control_ack(self, ack: ControlAck, now: float) -> bytes | None:
        if not self.connected:
            return None
        if ack.session != self.session:
            self.stale_acks += 1
            # The firmware armed under a session we are not using: stop it.
            return self._control("disarm") if ack.armed else None
        if not self._fresh_seq(ack.seq):
            return None
        if ack.type == "disarm_ack" or not ack.armed:
            if self.armed or self._arm_seq is not None:
                self._disarm_locally("firmware_disarmed")
            return None
        if self._arm_seq is not None and ack.seq == self._arm_seq:
            self.armed = True
            self._arm_seq = None
            self._ack_deadline_from = now
            self.fault = None
            return None
        # An arm_ack we did not request must not arm the Pi side.
        return self.disarm("unexpected_arm")

    def _on_accessory_ack(self, ack: AccessoryAck) -> None:
        if not self.connected or ack.session != self.session:
            self.stale_acks += 1
            return
        if self._fresh_seq(ack.seq):
            self.accessories = {"buzzer": ack.buzzer, "light": ack.light}

    def _on_fault(self, fault: FirmwareFault) -> bytes | None:
        # Faults carry no session and always mean the firmware disarmed.
        if fault.reason == "boot":
            # A reboot starts with the buzzer and light off.
            self.accessories = {"buzzer": False, "light": False}
        if self.connected and (self.armed or self._arm_seq is not None):
            self._disarm_locally(fault.reason)
        return None

    def _on_imu(self, imu: ImuTelemetry) -> bytes | None:
        previous = self.latest_imu
        self.latest_imu = imu
        self.imu_messages += 1
        if previous is not None and imu.timestamp_ms < previous.timestamp_ms:
            # millis() restarted: the firmware rebooted and lost its session.
            self.accessories = {"buzzer": False, "light": False}
            if self.connected and (self.armed or self._arm_seq is not None):
                self._disarm_locally("firmware_reset")
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
            accessories=dict(self.accessories),
        )
