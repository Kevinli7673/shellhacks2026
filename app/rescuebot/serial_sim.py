"""Host-side simulation of the ESP32-S2 serial behavior.

Mirrors the firmware on feature/esp32-controller (3cbea5f: controller.cpp,
session_guard.cpp, protocol_codec.cpp) for protocol tests and simulated
communication failures. It applies the same mixing reference as the mock
backend and must pass fixtures/serial_protocol_vectors.json like the
firmware. It is not firmware.
"""

from __future__ import annotations

from .mecanum import mix_mecanum
from .serial_protocol import (
    MAX_PWM,
    ControlAck,
    ControlCommand,
    DriveAck,
    DriveCommand,
    FirmwareFault,
    ImuTelemetry,
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

    def reboot(self) -> list[bytes]:
        """Power-on state: no session, disarmed, outputs stopped; announces boot."""
        self.boot_count += 1
        self._decoder = LineDecoder()
        self.session: str | None = None
        self.armed = False
        self.last_seq = 0
        self.last_valid_at: float | None = None
        self.outputs = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}
        self.rejected_lines = 0
        return [FirmwareFault("boot").encode()]

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
            replies.extend(self._reject("oversized_packet"))
        for line in lines:
            replies.extend(self.handle_line(line, now))
        return replies

    def handle_line(self, line: bytes, now: float) -> list[bytes]:
        try:
            command = parse_command(line)
        except ProtocolError:
            return self._reject("malformed_packet")
        if isinstance(command, ControlCommand):
            return self._handle_control(command, now)
        return self._handle_drive(command, now)

    def _reject(self, reason: str) -> list[bytes]:
        # Malformed input never drives; it stops, disarms, and always reports.
        self.rejected_lines += 1
        self._stop()
        return [FirmwareFault(reason).encode()]

    def _handle_control(self, command: ControlCommand, now: float) -> list[bytes]:
        if command.type == "disarm":
            # Stop overrides everything: any session, any seq.
            self._stop()
            return [ControlAck("disarm_ack", command.session, command.seq, False).encode()]
        if command.session == self.session and command.seq <= self.last_seq:
            return []  # stale or replayed arm: no ack, no state change
        # Any other arm adopts its session and seq as the new baseline and
        # zeroes outputs, so nothing moves until the next drive packet.
        self.session = command.session
        self.last_seq = command.seq
        self.armed = True
        self.last_valid_at = now
        self.outputs = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}
        return [ControlAck("arm_ack", command.session, command.seq, True).encode()]

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
                return [FirmwareFault("watchdog_expired").encode()]
        return []

    def imu(self, now: float, heading: float = 0.0) -> bytes:
        return ImuTelemetry(round(now * 1000), True, heading, 3).encode()
