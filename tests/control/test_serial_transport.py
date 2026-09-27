from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import unittest

from rescuebot.bridge_ipc import ArbiterCommand
from rescuebot.motor_bridge import MotorBridge, build_transport
from rescuebot.serial_link import MotorLink
from rescuebot.serial_protocol import BAUD_RATE, AccessoryCommand, ControlCommand, parse_command
from rescuebot.serial_transport import PySerialTransport, SerialTransportError, stable_serial_device


DEVICE = Path("/dev/serial/by-id/usb-ESP32-S2_Rescuebot")


@dataclass
class FakeSerial:
    inbound: bytearray = field(default_factory=bytearray)
    is_open: bool = True
    writes: list[bytes] = field(default_factory=list)
    next_write_result: int | None = None
    read_error: BaseException | None = None
    write_error: BaseException | None = None
    read_sizes: list[int] = field(default_factory=list)

    @property
    def in_waiting(self) -> int:
        return len(self.inbound)

    def write(self, data: bytes) -> int:
        if self.write_error is not None:
            raise self.write_error
        self.writes.append(data)
        return len(data) if self.next_write_result is None else self.next_write_result

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if self.read_error is not None:
            raise self.read_error
        data = bytes(self.inbound[:size])
        del self.inbound[:size]
        return data

    def close(self) -> None:
        self.is_open = False


class FakeFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.ports: list[FakeSerial] = []
        self.open_error: BaseException | None = None

    def __call__(self, **kwargs: object) -> FakeSerial:
        self.calls.append(kwargs)
        if self.open_error is not None:
            raise self.open_error
        port = FakeSerial()
        self.ports.append(port)
        return port


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now


class SerialTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = FakeFactory()
        self.transport = PySerialTransport(DEVICE, max_read_bytes=4, serial_factory=self.factory)

    def test_requires_a_direct_stable_serial_path(self) -> None:
        for path in ("/dev/ttyUSB0", "/dev/ttyACM0", "/dev/serial/by-id", "/dev/serial/by-id/../ttyUSB0"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                stable_serial_device(path)
        self.assertEqual(stable_serial_device(DEVICE), DEVICE)

    def test_opens_at_the_frozen_baudrate_with_no_serial_waits(self) -> None:
        self.assertTrue(self.transport.open())
        self.assertTrue(self.transport.connected)
        self.assertEqual(
            self.factory.calls,
            [
                {
                    "port": str(DEVICE),
                    "baudrate": BAUD_RATE,
                    "timeout": 0,
                    "write_timeout": 0,
                    "xonxoff": False,
                    "rtscts": False,
                    "dsrdtr": False,
                }
            ],
        )
        second_transport = PySerialTransport(DEVICE, serial_factory=self.factory)
        self.assertTrue(second_transport.open())
        self.assertTrue(second_transport.connected)
        with self.assertRaises(ValueError):
            PySerialTransport(DEVICE, baudrate=9600, serial_factory=self.factory)

    def test_read_is_bounded_and_write_has_no_backlog(self) -> None:
        self.assertTrue(self.transport.open())
        port = self.factory.ports[-1]
        port.inbound.extend(b"abcdef")
        self.assertEqual(self.transport.read(), b"abcd")
        self.assertEqual(self.transport.read(), b"ef")
        self.assertEqual(port.read_sizes, [4, 2])
        self.transport.write(b'{"type":"disarm"}\n')
        self.assertEqual(port.writes, [b'{"type":"disarm"}\n'])

    def test_failed_open_is_disconnected_and_retries_cleanly(self) -> None:
        self.factory.open_error = OSError("device unavailable")
        self.assertFalse(self.transport.open())
        self.assertFalse(self.transport.connected)
        self.assertIn("device unavailable", self.transport.last_error or "")
        self.factory.open_error = None
        self.assertTrue(self.transport.open())
        self.assertTrue(self.transport.connected)
        self.assertIsNone(self.transport.last_error)

    def test_short_write_closes_the_port_instead_of_retrying_old_data(self) -> None:
        self.assertTrue(self.transport.open())
        port = self.factory.ports[-1]
        port.next_write_result = 2
        with self.assertRaises(SerialTransportError):
            self.transport.write(b"abcdef")
        self.assertFalse(self.transport.connected)
        self.assertFalse(port.is_open)
        self.assertEqual(port.writes, [b"abcdef"])
        self.assertIn("partial serial write", self.transport.last_error or "")

    def test_read_error_closes_the_port(self) -> None:
        self.assertTrue(self.transport.open())
        port = self.factory.ports[-1]
        port.inbound.extend(b"x")
        port.read_error = OSError("cable removed")
        with self.assertRaises(SerialTransportError):
            self.transport.read()
        self.assertFalse(self.transport.connected)
        self.assertIn("serial read failed", self.transport.last_error or "")

    def test_build_transport_keeps_simulator_default_and_requires_serial_device(self) -> None:
        self.assertEqual(build_transport("sim").__class__.__name__, "SimulatedTransport")
        with self.assertRaises(ValueError):
            build_transport("serial")
        self.assertIsInstance(build_transport("serial", serial_device=DEVICE), PySerialTransport)


class SerialBridgeSafetyTests(unittest.TestCase):
    def test_unavailable_serial_is_reported_disarmed_with_a_bounded_fault(self) -> None:
        clock = FakeClock()
        factory = FakeFactory()
        factory.open_error = OSError("device unavailable")
        bridge = MotorBridge(PySerialTransport(DEVICE, serial_factory=factory))

        status = bridge.step([], clock.now)

        self.assertFalse(status.transport_connected)
        self.assertFalse(status.armed)
        self.assertFalse(status.arm_pending)
        self.assertEqual(status.fault, "transport_unavailable")
        self.assertEqual(factory.ports, [])

    def test_port_loss_discards_pending_arm_and_reconnects_disarmed(self) -> None:
        clock = FakeClock()
        factory = FakeFactory()
        transport = PySerialTransport(DEVICE, serial_factory=factory)
        sessions = iter(("pi-first", "pi-second"))
        bridge = MotorBridge(
            transport,
            MotorLink(session_factory=lambda: next(sessions)),
            reconnect_period_s=1.0,
        )

        bridge.step([], clock.now)
        first_session = bridge.link.session
        self.assertEqual(first_session, "pi-first")
        self.assertIsInstance(parse_command(factory.ports[0].writes[0][:-1]), ControlCommand)
        self.assertEqual(parse_command(factory.ports[0].writes[0][:-1]).type, "disarm")

        clock.now += 0.01
        arm = ArbiterCommand("arm", "browser", 1, clock.now + 0.25)
        bridge.step([arm.encode()], clock.now)
        self.assertTrue(bridge.link.snapshot(clock.now).arm_pending)

        factory.ports[0].is_open = False
        clock.now += 0.01
        status = bridge.step([], clock.now)
        self.assertFalse(status.transport_connected)
        self.assertFalse(status.armed)
        self.assertFalse(status.arm_pending)
        self.assertEqual(status.fault, "link_lost")

        clock.now += 1.0
        status = bridge.step([], clock.now)
        self.assertTrue(status.transport_connected)
        self.assertFalse(status.armed)
        self.assertFalse(status.arm_pending)
        self.assertEqual(bridge.link.session, "pi-second")
        self.assertEqual(len(factory.ports), 2)
        # A new session starts disarmed, then switches the buzzer and light off.
        self.assertEqual(len(factory.ports[1].writes), 2)
        self.assertEqual(parse_command(factory.ports[1].writes[0][:-1]).type, "disarm")
        accessories = parse_command(factory.ports[1].writes[1][:-1])
        self.assertIsInstance(accessories, AccessoryCommand)
        self.assertEqual((accessories.buzzer, accessories.light), (False, False))


if __name__ == "__main__":
    unittest.main()
