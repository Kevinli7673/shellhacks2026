import tempfile
import unittest
from pathlib import Path

from rescuebot.autonomy import AutonomyControl, AutonomyIntent
from rescuebot.autonomy_ipc import AutonomyHostEndpoint, decode_autonomy_status, encode_autonomy_intent
from rescuebot.bridge_ipc import DatagramReceiver, DatagramSender
from rescuebot.control import ManualControl
from rescuebot.gazebo_backend import GazeboMotorBackend
from rescuebot.service import RobotControlService


class AutonomyControlTests(unittest.TestCase):
    def test_start_waits_briefly_for_first_command_then_times_out(self) -> None:
        autonomy = AutonomyControl(timeout_s=0.25)
        autonomy.start("mission", now=1.0)
        self.assertEqual(autonomy.motion(now=1.1).forward, 0.0)
        self.assertIsNone(autonomy.motion(now=1.25))
        self.assertEqual(autonomy.status().reason, "autonomy_timeout")

    def test_only_current_mission_and_advancing_sequence_are_accepted(self) -> None:
        autonomy = AutonomyControl()
        autonomy.start("mission", now=0.0)
        self.assertFalse(autonomy.receive(AutonomyIntent("other", 1, 1.0, 1, 0, 0)))
        self.assertTrue(autonomy.receive(AutonomyIntent("mission", 3, 1.0, 1, 0, 0)))
        self.assertFalse(autonomy.receive(AutonomyIntent("mission", 3, 1.1, 0, 1, 0)))
        self.assertEqual(autonomy.motion(now=0.5).forward, 1.0)


class HostArbitrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.sim_receiver = DatagramReceiver(root / "sim.sock")
        self.addCleanup(self.sim_receiver.close)
        self.backend = GazeboMotorBackend(root / "sim.sock")
        self.addCleanup(self.backend.close)
        self.control = ManualControl(pwm_ceiling=100, input_timeout_s=0.25)
        self.service = RobotControlService(self.control, self.backend, allow_autonomy=True)
        self.session = "operator"
        self.assertTrue(self.service.claim(self.session))
        self.assertTrue(self.service.enable(self.session, now=1.0))

    def test_explicit_autonomy_uses_host_mixing_then_manual_cancels_without_resume(self) -> None:
        mission = self.service.start_autonomy(self.session)
        self.assertIsNotNone(mission)
        assert mission is not None
        self.assertTrue(self.service.autonomy.receive(AutonomyIntent(mission, 1, 1.3, 1.0, 0.0, 0.0)))
        snapshot = self.service.tick(now=1.1)
        self.assertEqual(snapshot.source, "autonomy")
        self.assertEqual(snapshot.wheels.as_dict(), {"fl": 20, "fr": 20, "rl": 20, "rr": 20})
        command = self.sim_receiver.drain()[-1]
        self.assertIn(b'"armed":true', command)

        self.assertTrue(self.service.keys(self.session, ["KeyD"], now=1.12))
        manual = self.service.tick(now=1.12)
        self.assertEqual(manual.source, "manual")
        self.assertFalse(self.service.autonomy.active)
        self.assertEqual(self.service.autonomy.status().reason, "manual_override")

        self.assertTrue(self.service.keys(self.session, [], now=1.13))
        stopped = self.service.tick(now=1.13)
        self.assertEqual(stopped.source, "manual")
        self.assertTrue(stopped.intent.is_stopped)

    def test_stale_autonomy_command_disarms_the_host(self) -> None:
        mission = self.service.start_autonomy(self.session)
        assert mission is not None
        self.service.autonomy.receive(AutonomyIntent(mission, 1, 1.2, 0.5, 0.0, 0.0))
        self.service.tick(now=1.1)
        expired = self.service.tick(now=1.2)
        self.assertFalse(expired.armed)
        self.assertEqual(expired.fault, "autonomy_timeout")

    def test_non_simulation_backend_cannot_start_autonomy(self) -> None:
        manual = ManualControl(pwm_ceiling=100)
        self.assertTrue(manual.claim("operator"))
        self.assertTrue(manual.enable("operator", now=0.0))
        service = RobotControlService(manual, allow_autonomy=False)
        self.assertIsNone(service.start_autonomy("operator"))


class AutonomyIpcTests(unittest.TestCase):
    def test_host_endpoint_accepts_valid_intent_and_publishes_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            endpoint = AutonomyHostEndpoint(root / "commands.sock", root / "status.sock")
            self.addCleanup(endpoint.close)
            command_sender = DatagramSender(root / "commands.sock")
            self.addCleanup(command_sender.close)
            status_receiver = DatagramReceiver(root / "status.sock")
            self.addCleanup(status_receiver.close)
            intent = AutonomyIntent("mission", 1, 10.0, 0.1, -0.2, 0.3)
            self.assertTrue(command_sender.send(encode_autonomy_intent(intent)))
            self.assertEqual(endpoint.drain(), [intent])
            endpoint.publish(AutonomyControl().status())
            status = decode_autonomy_status(status_receiver.drain()[0])
            self.assertFalse(status.active)


if __name__ == "__main__":
    unittest.main()
