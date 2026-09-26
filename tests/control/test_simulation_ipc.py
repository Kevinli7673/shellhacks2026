import json
import tempfile
import unittest
from pathlib import Path

from rescuebot.control import ManualControl
from rescuebot.bridge_ipc import DatagramReceiver
from rescuebot.gazebo_backend import GazeboMotorBackend
from rescuebot.simulation_ipc import SimulationCommand, SimulationIpcError


class SimulationCommandTests(unittest.TestCase):
    def test_round_trip_preserves_selected_logical_axes(self) -> None:
        command = SimulationCommand(7, 10.25, True, 0.5, -0.25, 1.0)
        self.assertEqual(SimulationCommand.decode(command.encode()), command)

    def test_rejects_stale_unsafe_shape_and_nonfinite_values(self) -> None:
        with self.assertRaises(SimulationIpcError):
            SimulationCommand.decode(b'{"type":"simulation_command"}')
        data = json.loads(SimulationCommand(1, 1.0, False).encode())
        data["forward"] = float("nan")
        with self.assertRaises(SimulationIpcError):
            SimulationCommand.decode(json.dumps(data).encode())

    def test_command_freshness_is_strict(self) -> None:
        command = SimulationCommand(1, 4.0, True)
        self.assertTrue(command.is_fresh(3.999))
        self.assertFalse(command.is_fresh(4.0))


class GazeboBackendTests(unittest.TestCase):
    def test_snapshot_is_sent_as_a_logical_command_not_wheel_mixing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket = Path(directory) / "sim.sock"
            receiver = DatagramReceiver(socket)
            self.addCleanup(receiver.close)
            backend = GazeboMotorBackend(socket)
            self.addCleanup(backend.close)
            control = ManualControl(pwm_ceiling=100)
            self.assertTrue(control.claim("operator"))
            self.assertTrue(control.enable("operator", now=1.0))
            self.assertTrue(control.set_keys("operator", ["KeyW", "KeyD"], now=1.1))
            snapshot = control.snapshot(now=1.1)
            backend.apply_snapshot(snapshot, "manual", now=1.1)
            self.assertEqual(backend.wheels.as_dict(), {"fl": 30, "fr": 0, "rl": 0, "rr": 30})
            self.assertEqual(backend.last_reason, "manual")
            self.assertTrue(backend.healthy)
            command = SimulationCommand.decode(receiver.drain()[0])
            self.assertTrue(command.armed)
            self.assertEqual((command.forward, command.sideways, command.turn), (1.0, 1.0, 0.0))

    def test_disarmed_snapshot_forces_zero_logical_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = GazeboMotorBackend(Path(directory) / "sim.sock")
            self.addCleanup(backend.close)
            control = ManualControl(pwm_ceiling=100)
            snapshot = control.snapshot(now=2.0)
            backend.apply_snapshot(snapshot, "operator_stop", now=2.0)
            self.assertEqual(backend.wheels.as_dict(), {"fl": 0, "fr": 0, "rl": 0, "rr": 0})


if __name__ == "__main__":
    unittest.main()
