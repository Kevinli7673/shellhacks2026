"""Independent motor-bridge process: arbiter commands in, ESP32 serial out.

The bridge owns the serial link. It forwards only the newest fresh arbiter
command at a fixed 20 Hz cadence, disarms after 250 ms without a fresh
arbiter command or an advancing ACK, handles stop immediately, and never
arms on its own: arming needs an explicit "arm" command and the firmware's
arm_ack. A change of arbiter (control-service restart) disarms.

Buzzer and light requests pass straight through. A new serial session, a
change of arbiter, and bridge shutdown all switch them off.

Transports are pluggable. The simulator is the default; passing an explicit
stable serial-by-id path selects the real ESP32-S2 transport.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import select
import signal
import sys
import time
from typing import Callable, Protocol

from .bridge_ipc import (
    ArbiterCommand,
    BridgeStatus,
    DatagramReceiver,
    DatagramSender,
    reduce_commands,
)
from .serial_link import MotorLink
from .serial_protocol import ImuTelemetry
from .serial_sim import SimulatedFirmware
from .serial_transport import PySerialTransport


ARBITER_TIMEOUT_S = 0.250
DRIVE_PERIOD_S = 0.050
RECONNECT_PERIOD_S = 1.0
LOOP_PERIOD_S = 0.010


class Transport(Protocol):
    """Byte stream to the ESP32. All methods must return without blocking."""

    connected: bool

    def open(self) -> bool: ...

    def write(self, data: bytes) -> None: ...

    def read(self) -> bytes: ...

    def close(self) -> None: ...


class SimulatedTransport:
    """Loops bytes through SimulatedFirmware on a shared clock."""

    def __init__(
        self,
        firmware: SimulatedFirmware | None = None,
        clock: Callable[[], float] = time.monotonic,
        imu_period_s: float = 0.050,
    ) -> None:
        self.firmware = firmware or SimulatedFirmware()
        self.clock = clock
        self.imu_period_s = imu_period_s
        self.connected = False
        self._pending = bytearray()
        self._last_imu_at: float | None = None

    def open(self) -> bool:
        self.connected = True
        return True

    def write(self, data: bytes) -> None:
        if not self.connected:
            raise OSError("simulated transport closed")
        for line in self.firmware.receive(data, self.clock()):
            self._pending.extend(line)

    def read(self) -> bytes:
        if not self.connected:
            raise OSError("simulated transport closed")
        now = self.clock()
        for line in self.firmware.tick(now):
            self._pending.extend(line)
        if self._last_imu_at is None or now - self._last_imu_at >= self.imu_period_s:
            self._last_imu_at = now
            self._pending.extend(self.firmware.imu(now))
        data = bytes(self._pending)
        self._pending.clear()
        return data

    def close(self) -> None:
        self.connected = False


class MotorBridge:
    def __init__(
        self,
        transport: Transport,
        link: MotorLink | None = None,
        arbiter_timeout_s: float = ARBITER_TIMEOUT_S,
        drive_period_s: float = DRIVE_PERIOD_S,
        reconnect_period_s: float = RECONNECT_PERIOD_S,
    ) -> None:
        self.transport = transport
        self.link = link or MotorLink()
        self.arbiter_timeout_s = arbiter_timeout_s
        self.drive_period_s = drive_period_s
        self.reconnect_period_s = reconnect_period_s
        self.arbiter: str | None = None
        self._arbiter_seq = 0
        self._drive: ArbiterCommand | None = None
        self._last_arbiter_at: float | None = None
        self._last_drive_sent_at: float | None = None
        self._last_connect_attempt: float | None = None
        self.rejected_commands = 0
        self._diagnostic_tail = b""
        self.fault: str | None = None

    # -- transport ---------------------------------------------------------

    def _ensure_connected(self, now: float) -> None:
        if self.transport.connected:
            return
        if self.link.connected:
            # The transport closed without raising: never report a dead link as armed.
            self._transport_lost()
        if (
            self._last_connect_attempt is not None
            and now - self._last_connect_attempt < self.reconnect_period_s
        ):
            return
        self._last_connect_attempt = now
        try:
            opened = self.transport.open()
        except OSError:
            opened = False
        if opened:
            # A fresh session, disarmed; reconnecting never re-arms and never
            # keeps a previous session's buzzer or light on.
            self._write(self.link.connect())
            self._write(self.link.set_accessories(False, False))
        else:
            # A port that cannot open is a visible, disarmed safety state.
            # Keep the transport's detailed OS error local; bridge status uses
            # a bounded, stable fault code that the dashboard can display.
            self.link.fault = "transport_unavailable"

    def _write(self, data: bytes | None) -> None:
        if data is None or not self.transport.connected:
            return
        try:
            self.transport.write(data)
        except OSError:
            self._transport_lost()

    def _transport_lost(self) -> None:
        self.transport.close()
        self.link.disconnect()
        self._drive = None

    def _read(self, now: float) -> None:
        if not self.transport.connected:
            return
        try:
            data = self.transport.read()
        except OSError:
            self._transport_lost()
            return
        if data:
            self._log_rejects(data)
            for reply in self.link.receive(data, now):
                self._write(reply)

    def _log_rejects(self, data: bytes) -> None:
        """Print the firmware's rx_reject echo: the exact line it could not parse."""
        *lines, self._diagnostic_tail = (self._diagnostic_tail + data).split(b"\n")
        self._diagnostic_tail = self._diagnostic_tail[-1024:]
        for line in lines:
            if b'"rx_reject"' in line:
                print(f"[bridge] firmware rejected: {line.decode('utf-8', 'replace')}",
                      file=sys.stderr, flush=True)

    # -- arbiter commands --------------------------------------------------

    def _accept(self, command: ArbiterCommand) -> bool:
        if command.arbiter != self.arbiter:
            if self.arbiter is not None:
                # The control service restarted: never carry arming, or the
                # previous operator's buzzer and light, across.
                self._write(self.link.disarm("arbiter_changed"))
                self._write(self.link.set_accessories(False, False))
                self._drive = None
            self.arbiter = command.arbiter
            self._arbiter_seq = 0
        if command.seq <= self._arbiter_seq:
            self.rejected_commands += 1
            return False
        self._arbiter_seq = command.seq
        return True

    def _handle(self, command: ArbiterCommand, now: float) -> None:
        if not self._accept(command):
            return
        if command.kind == "stop":
            self._drive = None
            if self.link.armed or self.link.snapshot(now).arm_pending:
                self._write(self.link.disarm("arbiter_stop"))
            return
        if not command.is_fresh(now):
            self.rejected_commands += 1
            return
        if command.kind == "accessories":
            # Not a driving intent, so it must not refresh arbiter freshness.
            self._write(self.link.set_accessories(command.buzzer, command.light))
            return
        self._last_arbiter_at = now
        if command.kind == "arm":
            if self.link.connected and not self.link.armed:
                self._drive = None
                self._write(self.link.request_arm())
            return
        self._drive = command

    # -- main step -----------------------------------------------------------

    def step(self, datagrams: list[bytes], now: float) -> BridgeStatus:
        self._ensure_connected(now)
        self._read(now)

        batch = reduce_commands(datagrams)
        self.rejected_commands += batch.rejected
        for command in batch.controls:
            self._handle(command, now)
        if batch.latest_drive is not None:
            self._handle(batch.latest_drive, now)

        engaged = self.link.armed or self.link.snapshot(now).arm_pending
        if engaged:
            stale_arbiter = (
                self._last_arbiter_at is None
                or now - self._last_arbiter_at > self.arbiter_timeout_s
                or (self._drive is not None and not self._drive.is_fresh(now))
            )
            if stale_arbiter:
                self._drive = None
                self._write(self.link.disarm("arbiter_timeout"))

        self._write(self.link.check(now))
        if (
            self.link.armed
            and self._drive is not None
            and (
                self._last_drive_sent_at is None
                or now - self._last_drive_sent_at >= self.drive_period_s
            )
        ):
            drive = self._drive
            self._last_drive_sent_at = now
            self._write(
                self.link.drive(drive.forward, drive.sideways, drive.turn, drive.speed_limit, now)
            )
        return self.status(now)

    def status(self, now: float) -> BridgeStatus:
        snapshot = self.link.snapshot(now)
        imu: ImuTelemetry | None = self.link.latest_imu
        return BridgeStatus(
            sent_at=now,
            transport_connected=self.transport.connected,
            armed=snapshot.armed,
            arm_pending=snapshot.arm_pending,
            fault=snapshot.fault,
            ack_age_ms=snapshot.ack_age_ms,
            wheels=snapshot.wheels,
            arbiter=self.arbiter,
            rejected_commands=self.rejected_commands,
            imu=None
            if imu is None
            else {
                "timestamp_ms": imu.timestamp_ms,
                "available": imu.available,
                "heading": imu.heading,
                "calibration": imu.calibration,
            },
            accessories=snapshot.accessories,
        )

    def shutdown(self) -> None:
        self._write(self.link.disarm("bridge_shutdown"))
        self._write(self.link.set_accessories(False, False))
        self.transport.close()


# ---------------------------------------------------------------------------
# Process entry point.


def default_run_dir() -> Path:
    return Path(os.environ.get("RESCUEBOT_RUN_DIR", f"/tmp/rescuebot-{os.getuid()}"))


def build_transport(
    transport_name: str,
    *,
    sim_ceiling: int = 255,
    serial_device: Path | None = None,
) -> Transport:
    """Build the selected link without making a hardware connection yet."""
    if transport_name == "sim":
        return SimulatedTransport(SimulatedFirmware(hardware_ceiling=sim_ceiling))
    if transport_name == "serial":
        if serial_device is None:
            raise ValueError("--serial-device is required with --transport serial")
        return PySerialTransport(serial_device)
    raise ValueError(f"unknown transport {transport_name!r}")


def run(
    bridge: MotorBridge,
    command_socket: Path,
    status_socket: Path,
    should_stop: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    receiver = DatagramReceiver(command_socket)
    status_sender = DatagramSender(status_socket)
    try:
        while not should_stop():
            readable, _, _ = select.select([receiver], [], [], LOOP_PERIOD_S)
            datagrams = receiver.drain() if readable else []
            status = bridge.step(datagrams, clock())
            status_sender.send(status.encode())
    finally:
        bridge.shutdown()
        receiver.close()
        status_sender.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Rescuebot motor bridge.")
    run_dir = default_run_dir()
    parser.add_argument("--command-socket", type=Path, default=run_dir / "bridge-command.sock")
    parser.add_argument("--status-socket", type=Path, default=run_dir / "bridge-status.sock")
    parser.add_argument(
        "--transport",
        choices=("sim", "serial"),
        default="sim",
        help="sim: in-process simulated firmware; serial: explicit ESP32-S2 USB link.",
    )
    parser.add_argument(
        "--serial-device",
        type=Path,
        help="ESP32-S2 path under /dev/serial/by-id/ (required for --transport serial).",
    )
    parser.add_argument(
        "--sim-ceiling",
        type=int,
        default=255,
        help="Simulated firmware hardware PWM ceiling.",
    )
    args = parser.parse_args(argv)
    for path in (args.command_socket, args.status_socket):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    try:
        transport = build_transport(
            args.transport,
            sim_ceiling=args.sim_ceiling,
            serial_device=args.serial_device,
        )
    except ValueError as exc:
        parser.error(str(exc))
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    # SIGTERM (systemd, process managers) and Ctrl+C both end the loop cleanly,
    # so shutdown() sends a final disarm before the port closes.
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run(MotorBridge(transport), args.command_socket, args.status_socket, lambda: stopping)


if __name__ == "__main__":
    main()
