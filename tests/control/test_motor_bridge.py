import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from rescuebot.bridge_ipc import (
    ArbiterCommand,
    BridgeStatus,
    DatagramReceiver,
    DatagramSender,
    IpcError,
    reduce_commands,
)
from rescuebot.motor_bridge import MotorBridge, SimulatedTransport
from rescuebot.serial_link import MotorLink
from rescuebot.serial_sim import SimulatedFirmware


APP_DIR = Path(__file__).parents[2] / "app"
STOPPED = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Arbiter:
    """Builds commands the way the control service will."""

    def __init__(self, clock: FakeClock, arbiter: str = "svc-a") -> None:
        self.clock = clock
        self.arbiter = arbiter
        self.seq = 0

    def command(self, kind: str, forward: float = 0.0, ttl: float = 0.25, speed_limit: int = 100) -> bytes:
        self.seq += 1
        return ArbiterCommand(
            kind=kind,
            arbiter=self.arbiter,
            seq=self.seq,
            expires_at=self.clock.now + ttl,
            forward=forward,
            speed_limit=speed_limit,
        ).encode()

    def accessories(self, buzzer: bool, light: bool, ttl: float = 0.25) -> bytes:
        self.seq += 1
        return ArbiterCommand(
            kind="accessories",
            arbiter=self.arbiter,
            seq=self.seq,
            expires_at=self.clock.now + ttl,
            buzzer=buzzer,
            light=light,
        ).encode()


def short_tmpdir() -> str:
    # macOS limits AF_UNIX paths to 104 bytes; the default TMPDIR is long.
    return tempfile.mkdtemp(prefix="rb", dir="/tmp")


class IpcTests(unittest.TestCase):
    def test_command_round_trip_and_validation(self) -> None:
        command = ArbiterCommand("drive", "svc", 3, 12.5, forward=1.0, speed_limit=60)
        self.assertEqual(ArbiterCommand.decode(command.encode()), command)
        for bad in [
            b'{"kind":"boost","arbiter":"svc","seq":1,"expires_at":1}',
            b'{"kind":"drive","arbiter":"svc","seq":0,"expires_at":1}',
            b'{"kind":"drive","arbiter":"svc","seq":1,"expires_at":1,"forward":1.5}',
            b'{"kind":"drive","arbiter":"svc","seq":1,"expires_at":NaN}',
            b'{"kind":"drive","arbiter":"svc","seq":1,"expires_at":1,"speed_limit":60.5}',
            b'{"kind":"drive","arbiter":"svc","seq":1,"expires_at":1,"motor":"fl"}',
            b"not json",
            b"[1]",
        ]:
            with self.subTest(bad=bad), self.assertRaises(IpcError):
                ArbiterCommand.decode(bad)

    def test_stop_supersedes_earlier_drives_and_only_the_newest_drive_is_kept(self) -> None:
        clock = FakeClock()
        arbiter = Arbiter(clock)
        batch = reduce_commands(
            [
                arbiter.command("drive", 1.0),
                arbiter.command("drive", 0.5),
                arbiter.command("stop"),
                b"garbage",
                arbiter.command("arm"),
                arbiter.command("drive", 0.2),
                arbiter.command("drive", -0.3),
            ]
        )
        self.assertEqual([c.kind for c in batch.controls], ["stop", "arm"])
        self.assertIsNotNone(batch.latest_drive)
        self.assertEqual(batch.latest_drive.forward, -0.3)
        self.assertEqual(batch.rejected, 1)

    def test_stop_after_a_drive_in_the_same_drain_drops_that_drive(self) -> None:
        arbiter = Arbiter(FakeClock())
        batch = reduce_commands([arbiter.command("drive", 1.0), arbiter.command("stop")])
        self.assertEqual([c.kind for c in batch.controls], ["stop"])
        self.assertIsNone(batch.latest_drive)

    def test_sender_never_blocks_or_raises_without_a_receiver(self) -> None:
        directory = short_tmpdir()
        self.addCleanup(shutil.rmtree, directory, True)
        sender = DatagramSender(Path(directory) / "missing.sock")
        self.addCleanup(sender.close)
        self.assertFalse(sender.send(b"{}"))
        self.assertEqual(sender.dropped_count, 1)

    def test_full_receiver_drops_instead_of_blocking(self) -> None:
        directory = short_tmpdir()
        self.addCleanup(shutil.rmtree, directory, True)
        receiver = DatagramReceiver(Path(directory) / "r.sock", max_drain=8)
        self.addCleanup(receiver.close)
        sender = DatagramSender(receiver.path)
        self.addCleanup(sender.close)
        payload = ArbiterCommand("drive", "svc", 1, 1.0).encode()
        started = time.monotonic()
        results = [sender.send(payload) for _ in range(2000)]
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIn(False, results)
        self.assertEqual(sender.dropped_count, results.count(False))
        self.assertEqual(len(receiver.drain()), 8)  # drain is bounded per call

    def test_accessory_command_and_status_round_trip_and_validation(self) -> None:
        command = ArbiterCommand("accessories", "svc", 1, 1.0, buzzer=True, light=False)
        self.assertEqual(ArbiterCommand.decode(command.encode()), command)
        with self.assertRaises(IpcError):
            ArbiterCommand.decode(b'{"kind":"accessories","arbiter":"svc","seq":1,"expires_at":1,"buzzer":1}')
        status = BridgeStatus(1.5, True, False, False, None, None, accessories={"buzzer": True, "light": False})
        self.assertEqual(BridgeStatus.decode(status.encode()), status)
        for bad in (b'"yes"', b'{"buzzer":true}', b'{"buzzer":1,"light":false}'):
            with self.subTest(accessories=bad), self.assertRaises(IpcError):
                BridgeStatus.decode(
                    b'{"sent_at":1,"transport_connected":true,"armed":false,"arm_pending":false,'
                    b'"fault":null,"ack_age_ms":null,"accessories":' + bad + b"}"
                )

    def test_status_round_trip(self) -> None:
        status = BridgeStatus(1.5, True, True, False, None, 20, {"fl": 1, "fr": 2, "rl": 3, "rr": 4}, "svc")
        self.assertEqual(BridgeStatus.decode(status.encode()), status)
        with self.assertRaises(IpcError):
            BridgeStatus.decode(b'{"sent_at":1}')


class BridgeHarness:
    def __init__(self, hardware_ceiling: int = 255) -> None:
        self.clock = FakeClock()
        self.firmware = SimulatedFirmware(hardware_ceiling=hardware_ceiling)
        self.transport = SimulatedTransport(self.firmware, clock=self.clock)
        sessions = (f"pi{i:02d}" for i in range(1, 100))
        self.bridge = MotorBridge(self.transport, MotorLink(session_factory=lambda: next(sessions)))
        self.arbiter = Arbiter(self.clock)
        self.status = self.bridge.step([], self.clock.now)

    def step(self, *datagrams: bytes, advance: float = 0.01) -> BridgeStatus:
        self.clock.now += advance
        self.status = self.bridge.step(list(datagrams), self.clock.now)
        return self.status

    def arm(self) -> None:
        self.step(self.arbiter.command("arm"))
        self.step()

    def drive_for(self, seconds: float, forward: float = 1.0, period: float = 0.05) -> None:
        for _ in range(round(seconds / period)):
            self.clock.now += period  # the command is created when it is delivered
            self.step(self.arbiter.command("drive", forward), advance=0.0)


class MotorBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = BridgeHarness()

    def test_start_connects_disarmed_and_drive_alone_never_arms(self) -> None:
        self.assertTrue(self.h.status.transport_connected)
        self.assertFalse(self.h.status.armed)
        self.h.drive_for(0.5)
        self.assertFalse(self.h.firmware.armed)
        self.assertEqual(self.h.firmware.outputs, STOPPED)

    def test_explicit_arm_then_drive_reaches_the_wheels(self) -> None:
        self.h.arm()
        self.assertTrue(self.h.status.armed)
        self.h.drive_for(0.2)
        self.assertEqual(self.h.firmware.outputs, {"fl": 100, "fr": 100, "rl": 100, "rr": 100})
        self.assertEqual(self.h.status.wheels, self.h.firmware.outputs)
        self.assertIsNotNone(self.h.status.imu)

    def test_arbiter_silence_disarms_after_250ms(self) -> None:
        self.h.arm()
        self.h.drive_for(0.1)
        for _ in range(24):
            self.h.step()  # 0.24 s of silence
        self.assertTrue(self.h.status.armed)
        self.h.step(advance=0.02)
        self.assertFalse(self.h.status.armed)
        self.assertEqual(self.h.status.fault, "arbiter_timeout")
        self.assertFalse(self.h.firmware.armed)

    def test_receipt_deadline_holds_even_if_commands_claim_long_expiry(self) -> None:
        self.h.arm()
        self.h.clock.now += 0.05
        self.h.step(self.h.arbiter.command("drive", 1.0, ttl=10.0), advance=0.0)
        for _ in range(24):
            self.h.step()
        self.assertTrue(self.h.status.armed)
        self.h.step(advance=0.02)
        self.assertFalse(self.h.status.armed)
        self.assertEqual(self.h.status.fault, "arbiter_timeout")

    def test_drive_packets_go_out_at_20hz_regardless_of_poll_rate(self) -> None:
        self.h.arm()
        sent_before = self.h.bridge.link.last_sent_seq
        for _ in range(20):  # 200 ms of 10 ms polls with fresh intent every poll
            self.h.clock.now += 0.01
            self.h.step(self.h.arbiter.command("drive", 1.0), advance=0.0)
        self.assertEqual(self.h.bridge.link.last_sent_seq - sent_before, 4)

    def test_expired_commands_are_not_driven(self) -> None:
        self.h.arm()
        self.h.step(self.h.arbiter.command("drive", 1.0, ttl=-0.01))
        self.h.step(advance=0.06)
        self.assertEqual(self.h.firmware.outputs, STOPPED)
        self.assertGreaterEqual(self.h.status.rejected_commands, 1)

    def test_stop_disarms_in_the_same_step(self) -> None:
        self.h.arm()
        self.h.drive_for(0.1)
        self.h.step(self.h.arbiter.command("stop"), advance=0.001)
        self.assertFalse(self.h.firmware.armed)
        self.assertEqual(self.h.firmware.outputs, STOPPED)
        self.h.drive_for(0.2)  # drives after stop need a new explicit arm
        self.assertFalse(self.h.firmware.armed)

    def test_backlog_of_drives_sends_only_the_newest_once_per_period(self) -> None:
        self.h.arm()
        sent_before = self.h.bridge.link.last_sent_seq
        burst = [self.h.arbiter.command("drive", f / 10) for f in range(10)]
        self.h.step(*burst, advance=0.05)
        self.assertEqual(self.h.bridge.link.last_sent_seq, sent_before + 1)
        self.assertEqual(self.h.firmware.outputs["fl"], 90)

    def test_restarted_arbiter_disarms_and_does_not_rearm(self) -> None:
        self.h.arm()
        self.h.drive_for(0.1)
        self.h.arbiter = Arbiter(self.h.clock, arbiter="svc-b")
        self.h.drive_for(0.2)
        self.assertFalse(self.h.firmware.armed)
        self.assertEqual(self.h.status.fault, "arbiter_changed")
        self.assertEqual(self.h.status.arbiter, "svc-b")

    def test_replayed_arbiter_seq_is_rejected(self) -> None:
        old = self.h.arbiter.command("arm")
        self.h.step(old)
        self.h.step(self.h.arbiter.command("stop"))
        self.h.step(old)
        self.assertFalse(self.h.firmware.armed)
        self.assertGreaterEqual(self.h.status.rejected_commands, 1)

    def test_firmware_reboot_disarms_and_requires_explicit_rearm(self) -> None:
        self.h.arm()
        self.h.drive_for(0.1)
        self.h.transport._pending.extend(self.h.firmware.reboot()[0])
        self.h.drive_for(0.2)
        self.assertFalse(self.h.status.armed)
        self.assertEqual(self.h.status.fault, "boot")
        self.assertEqual(self.h.firmware.outputs, STOPPED)
        self.h.arm()
        self.h.drive_for(0.1)
        self.assertEqual(self.h.firmware.outputs["fl"], 100)

    def test_transport_loss_reconnects_with_a_new_session_and_stays_disarmed(self) -> None:
        self.h.arm()
        self.h.drive_for(0.1)
        old_session = self.h.bridge.link.session
        self.h.transport.close()
        self.h.drive_for(0.1)
        self.assertFalse(self.h.status.transport_connected)
        self.assertFalse(self.h.status.armed)
        self.assertEqual(self.h.status.fault, "link_lost")
        self.h.drive_for(1.1)  # reconnect attempts are rate limited to 1 s
        self.assertTrue(self.h.status.transport_connected)
        self.assertNotEqual(self.h.bridge.link.session, old_session)
        self.assertFalse(self.h.status.armed)

    def test_hardware_ceiling_is_reported_in_status_wheels(self) -> None:
        h = BridgeHarness(hardware_ceiling=40)
        h.arm()
        h.drive_for(0.1)
        self.assertEqual(h.status.wheels, {"fl": 40, "fr": 40, "rl": 40, "rr": 40})

    def test_accessories_pass_through_while_disarmed(self) -> None:
        self.h.step(self.h.arbiter.accessories(True, False))
        self.h.step()
        self.assertEqual(self.h.firmware.accessories, {"buzzer": True, "light": False})
        self.assertEqual(self.h.status.accessories, {"buzzer": True, "light": False})
        self.assertFalse(self.h.firmware.armed)
        self.assertFalse(self.h.status.armed)

    def test_accessory_requests_do_not_keep_an_armed_bridge_alive(self) -> None:
        self.h.arm()
        self.h.drive_for(0.1)
        for _ in range(6):  # 0.3 s of accessory requests and no drive intent
            self.h.clock.now += 0.05
            self.h.step(self.h.arbiter.accessories(True, True), advance=0.0)
        self.assertFalse(self.h.status.armed)
        self.assertEqual(self.h.status.fault, "arbiter_timeout")
        self.assertEqual(self.h.firmware.accessories, {"buzzer": True, "light": True})

    def test_new_session_arbiter_change_and_shutdown_switch_accessories_off(self) -> None:
        on = {"buzzer": True, "light": True}
        off = {"buzzer": False, "light": False}

        self.h.step(self.h.arbiter.accessories(True, True))
        self.h.step()
        self.assertEqual(self.h.firmware.accessories, on)
        self.h.transport.close()
        self.h.step(advance=1.1)  # past the reconnect period: a fresh session
        self.h.step()
        self.assertEqual(self.h.firmware.accessories, off)

        self.h.step(self.h.arbiter.accessories(True, True))
        self.h.step()
        self.assertEqual(self.h.firmware.accessories, on)
        restarted = Arbiter(self.h.clock, "svc-b")  # control service restarted
        self.h.step(restarted.command("stop"))
        self.h.step()
        self.assertEqual(self.h.firmware.accessories, off)

        self.h.step(restarted.accessories(True, True))
        self.h.step()
        self.assertEqual(self.h.firmware.accessories, on)
        self.h.bridge.shutdown()
        self.assertEqual(self.h.firmware.accessories, off)


class BridgeProcessTests(unittest.TestCase):
    """Runs the bridge as a separate OS process over real Unix sockets."""

    def test_separate_process_arms_drives_and_stops(self) -> None:
        directory = Path(short_tmpdir())
        self.addCleanup(shutil.rmtree, directory, True)
        command_path = directory / "cmd.sock"
        status = DatagramReceiver(directory / "status.sock")
        self.addCleanup(status.close)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "rescuebot.motor_bridge",
                "--command-socket",
                str(command_path),
                "--status-socket",
                str(status.path),
            ],
            env={"PYTHONPATH": str(APP_DIR), "PATH": "/usr/bin:/bin"},
        )
        self.addCleanup(process.kill)
        sender = DatagramSender(command_path)
        self.addCleanup(sender.close)
        seq = 0

        def send(kind: str, forward: float = 0.0) -> None:
            nonlocal seq
            seq += 1
            sender.send(
                ArbiterCommand(kind, "proc-test", seq, time.monotonic() + 0.25, forward=forward, speed_limit=80).encode()
            )

        def latest_status(keepalive: str, predicate) -> BridgeStatus:
            # Like the control service: keep resending current intent each tick.
            # While arming, that intent is a zero drive; a stop would cancel the arm.
            deadline = time.monotonic() + 3.0
            latest = None
            while time.monotonic() < deadline:
                for data in status.drain():
                    latest = BridgeStatus.decode(data)
                if latest is not None and predicate(latest):
                    return latest
                send(keepalive)
                time.sleep(0.02)
            self.fail(f"bridge status never matched; last={latest}")

        latest_status("stop", lambda s: s.transport_connected)
        send("arm")
        armed = latest_status("drive", lambda s: s.armed)
        self.assertEqual(armed.arbiter, "proc-test")
        deadline = time.monotonic() + 3.0
        driving = None
        while time.monotonic() < deadline:
            send("drive", 1.0)
            time.sleep(0.04)
            for data in status.drain():
                driving = BridgeStatus.decode(data)
            if driving is not None and driving.wheels["fl"] == 80:
                break
        self.assertIsNotNone(driving)
        self.assertEqual(driving.wheels, {"fl": 80, "fr": 80, "rl": 80, "rr": 80})
        send("stop")
        stopped = latest_status("stop", lambda s: not s.armed)
        self.assertEqual(stopped.wheels, STOPPED)
        process.terminate()
        self.assertEqual(process.wait(timeout=3), 0)


if __name__ == "__main__":
    unittest.main()
