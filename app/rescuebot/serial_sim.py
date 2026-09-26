"""Host-side simulation of the proposed ESP32-S2 serial behavior.

Used for protocol tests and simulated communication failures. It applies the
same mixing reference as the mock backend and must pass the same
fixtures/serial_protocol_vectors.json as the firmware. It is not firmware.
"""

from __future__ import annotations

from .mecanum import mix_mecanum
from .serial_protocol import (
    MAX_PWM,
    ControlCommand,
    DriveAck,
    DriveCommand,
    FirmwareState,
    LineDecoder,
    ProtocolError,
    parse_command,
)


WATCHDOG_S = 0.500


class SimulatedFirmware:
    def __init__(self, watchdog_s: float = WATCHDOG_S, hardware_ceiling: int = MAX_PWM) -> None:
        if watchdog_s <= 0:
            raise ValueError("watchdog_s must be positive")
        if not 0 <= hardware_ceiling <= MAX_PWM:
            raise ValueError("hardware_ceiling must be between 0 and 255")
        self.watchdog_s = watchdog_s
        self.hardware_ceiling = hardware_ceiling
        self.boot_count = 0
        self.reboot()

    def reboot(self) -> bytes:
        """Power-on state: no session, disarmed, outputs stopped."""
        self.boot_count += 1
        self._decoder = LineDecoder()
        self.session: str | None = None
        self.armed = False
        self.last_seq = 0
        self.last_valid_at: float | None = None
        self.outputs = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}
        self.rejected_lines = 0
        return self._state(ack=None, fault="boot")

    def _state(self, ack: int | None, fault: str | None) -> bytes:
        return FirmwareState(self.session, self.armed, ack, fault).encode()

    def _stop(self) -> None:
        self.armed = False
        self.last_valid_at = None
        self.outputs = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}

    def receive(self, chunk: bytes, now: float) -> list[bytes]:
        """Consume bytes from the Pi; returns lines the firmware would emit."""
        replies: list[bytes] = []
        oversized_before = self._decoder.oversized_count
        lines = self._decoder.feed(chunk)
        for _ in range(self._decoder.oversized_count - oversized_before):
            replies.extend(self._reject("oversized"))
        for line in lines:
            replies.extend(self.handle_line(line, now))
        return replies

    def handle_line(self, line: bytes, now: float) -> list[bytes]:
        try:
            command = parse_command(line)
        except ProtocolError as exc:
            return self._reject(exc.reason)
        if isinstance(command, ControlCommand):
            return self._handle_control(command, now)
        return self._handle_drive(command, now)

    def _reject(self, reason: str) -> list[bytes]:
        # Malformed input never drives; it stops and disarms.
        self.rejected_lines += 1
        was_armed = self.armed
        self._stop()
        return [self._state(ack=None, fault="malformed")] if was_armed else []

    def _handle_control(self, command: ControlCommand, now: float) -> list[bytes]:
        if command.type == "disarm":
            # Any disarm stops and adopts its session; it can never cause motion.
            self._stop()
            self.session = command.session
            self.last_seq = command.seq
            return [self._state(ack=command.seq, fault=None)]
        if command.session != self.session or command.seq <= self.last_seq:
            return []
        self.last_seq = command.seq
        self.armed = True
        self.last_valid_at = now
        self.outputs = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}
        return [self._state(ack=command.seq, fault=None)]

    def _handle_drive(self, command: DriveCommand, now: float) -> list[bytes]:
        # Wrong-session, duplicate, out-of-order, and disarmed packets are
        # ignored: no motion, no watchdog refresh, no acknowledgment.
        if not self.armed or command.session != self.session or command.seq <= self.last_seq:
            return []
        self.last_seq = command.seq
        self.last_valid_at = now
        wheels = mix_mecanum(
            command.forward,
            command.sideways,
            command.turn,
            min(command.speed_limit, self.hardware_ceiling),
        )
        self.outputs = wheels.as_dict()
        return [DriveAck(command.session, command.seq, **self.outputs).encode()]

    def tick(self, now: float) -> list[bytes]:
        """Advance the watchdog; expiry stops outputs and reports it."""
        if self.armed and self.last_valid_at is not None:
            if now - self.last_valid_at > self.watchdog_s:
                self._stop()
                return [self._state(ack=None, fault="watchdog")]
        return []

    def imu(self, now: float, heading: float = 0.0) -> bytes:
        return (
            b'{"type":"imu","t_ms":%d,"heading":%.1f,"calibration":{"sys":3,"gyro":3,"accel":3,"mag":3}}\n'
            % (round(now * 1000), heading)
        )
