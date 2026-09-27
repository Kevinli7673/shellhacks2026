from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from rescuebot.bridge_backend import BridgeMotorBackend
from rescuebot.bridge_ipc import DatagramReceiver, DatagramSender
from rescuebot.control import ManualControl
from rescuebot.motor_bridge import MotorBridge, SimulatedTransport
from rescuebot.serial_sim import SimulatedFirmware
from rescuebot.service import RobotControlService
from rescuebot.web import create_app


STOPPED = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}
FORWARD_30 = {"fl": 30, "fr": 30, "rl": 30, "rr": 30}  # 100 PWM ceiling at 30%


class FakeClock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


class Rig:
    """Control service <-> real Unix sockets <-> in-process bridge + simulated firmware."""

    def __init__(self, test: unittest.TestCase) -> None:
        directory = Path(tempfile.mkdtemp(prefix="rb", dir="/tmp"))
        test.addCleanup(shutil.rmtree, directory, True)
        self.clock = FakeClock()
        self.firmware = SimulatedFirmware()
        self.bridge = MotorBridge(SimulatedTransport(self.firmware, clock=self.clock))
        self.bridge_inbox = DatagramReceiver(directory / "cmd.sock")
        self.backend = BridgeMotorBackend(directory / "cmd.sock", directory / "status.sock")
        self.bridge_outbox = DatagramSender(directory / "status.sock")
        for closer in (self.bridge_inbox.close, self.backend.close, self.bridge_outbox.close):
            test.addCleanup(closer)
        self.service = RobotControlService(
            ManualControl(pwm_ceiling=100, input_timeout_s=0.250), self.backend
        )
        self.session = "browser-one"
        self.held: list[str] = []
        self.bridge_running = True
        test.assertTrue(self.service.claim(self.session))

    def pump(self, steps: int = 1, dt: float = 0.01) -> None:
        """Advance time; the bridge and control loop each run once per step."""
        for _ in range(steps):
            self.clock.now += dt
            if self.bridge_running:
                status = self.bridge.step(self.bridge_inbox.drain(), self.clock.now)
                self.bridge_outbox.send(status.encode())
            if self.service.control.armed:
                self.service.keys(self.session, self.held, now=self.clock.now)  # 10 ms heartbeat
            self.service.tick(self.clock.now)

    def enable(self) -> bool:
        return self.service.enable(self.session, now=self.clock.now)

    def state(self) -> dict:
        return self.service.state(self.clock.now)


class BridgeBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig(self)

    def test_enable_is_refused_until_the_bridge_reports_status(self) -> None:
        self.rig.bridge_running = False
        self.rig.pump(3)
        self.assertFalse(self.rig.enable())
        self.assertEqual(self.rig.service.control.fault, "backend_unavailable")

    def test_enable_shows_arming_then_enabled_after_firmware_confirms(self) -> None:
        self.rig.pump(3)
        self.assertTrue(self.rig.enable())
        state = self.rig.state()
        self.assertTrue(state["control"]["arming"])
        self.assertFalse(self.rig.firmware.armed)
        self.rig.pump(3)
        state = self.rig.state()
        self.assertFalse(state["control"]["arming"])
        self.assertTrue(state["control"]["armed"])
        self.assertTrue(state["motor"]["firmware_armed"])
        self.assertTrue(self.rig.firmware.armed)

    def test_keys_held_during_arming_do_not_move_until_confirmed(self) -> None:
        self.rig.pump(3)
        self.assertTrue(self.rig.enable())
        self.rig.held = ["KeyW"]
        # Step 1: the bridge sends arm. Step 2: the firmware's arm_ack arms the
        # link and the bridge forwards the drive queued while arming, which
        # must still be zero because the service had not yet seen the ack.
        self.rig.pump(2)
        self.assertTrue(self.rig.firmware.armed)
        self.assertEqual(self.rig.firmware.outputs, STOPPED)
        self.rig.pump(10)
        self.assertEqual(self.rig.firmware.outputs, FORWARD_30)
        self.assertEqual(self.rig.state()["motor"]["wheels"], FORWARD_30)

    def test_no_firmware_confirmation_stops_with_arm_timeout(self) -> None:
        original = self.rig.firmware._handle_control
        self.rig.firmware._handle_control = (  # type: ignore[method-assign]
            lambda command, now: [] if command.type == "arm" else original(command, now)
        )
        self.rig.pump(3)
        self.assertTrue(self.rig.enable())
        self.rig.pump(99)  # 0.99 s
        self.assertTrue(self.rig.service.arming)
        self.rig.pump(2)
        self.assertFalse(self.rig.service.control.armed)
        self.assertEqual(self.rig.service.control.fault, "arm_timeout")
        self.assertEqual(self.rig.backend.last_kind, "stop")

    def test_firmware_reboot_while_driving_stops_control_until_re_enabled(self) -> None:
        self.rig.pump(3)
        self.rig.enable()
        self.rig.held = ["KeyW"]
        self.rig.pump(10)
        self.assertEqual(self.rig.firmware.outputs, FORWARD_30)
        self.rig.bridge.transport._pending.extend(self.rig.firmware.reboot()[0])
        self.rig.pump(5)
        self.assertFalse(self.rig.service.control.armed)
        self.assertEqual(self.rig.service.control.fault, "boot")
        self.rig.pump(20)
        self.assertFalse(self.rig.firmware.armed)
        self.assertEqual(self.rig.firmware.outputs, STOPPED)

    def test_bridge_process_dying_stops_control(self) -> None:
        self.rig.pump(3)
        self.rig.enable()
        self.rig.held = ["KeyW"]
        self.rig.pump(10)
        self.rig.bridge_running = False
        self.rig.pump(50)  # status is stale after 0.5 s
        self.assertTrue(self.rig.service.control.armed)
        self.rig.pump(2)
        self.assertFalse(self.rig.service.control.armed)
        self.assertEqual(self.rig.service.control.fault, "backend_unavailable")

    def test_stop_reaches_the_firmware_on_the_next_bridge_step(self) -> None:
        self.rig.pump(3)
        self.rig.enable()
        self.rig.held = ["KeyW"]
        self.rig.pump(10)
        self.rig.service.stop("operator_stop", now=self.rig.clock.now)
        self.rig.held = []
        self.rig.pump(1)
        self.assertFalse(self.rig.firmware.armed)
        self.assertEqual(self.rig.firmware.outputs, STOPPED)
        self.assertEqual(self.rig.service.control.fault, "operator_stop")

    def test_accessory_toggle_reaches_the_firmware_and_owner_disconnect_turns_it_off(self) -> None:
        self.assertFalse(self.rig.service.set_accessory(self.rig.session, "light", True, now=self.rig.clock.now))
        self.rig.pump(3)  # the bridge must report status before a request is accepted
        self.assertTrue(self.rig.service.set_accessory(self.rig.session, "light", True, now=self.rig.clock.now))
        self.rig.pump(3)
        self.assertEqual(self.rig.firmware.accessories, {"buzzer": False, "light": True})
        accessories = self.rig.state()["motor"]["accessories"]
        self.assertEqual(accessories, {"buzzer": False, "light": True, "available": True})
        self.assertFalse(self.rig.firmware.armed)
        self.rig.service.disconnect(self.rig.session, now=self.rig.clock.now)
        self.rig.pump(3)
        self.assertEqual(self.rig.firmware.accessories, {"buzzer": False, "light": False})

    def test_restarted_bridge_does_not_resume_driving_by_itself(self) -> None:
        self.rig.pump(3)
        self.rig.enable()
        self.rig.held = ["KeyW"]
        self.rig.pump(10)
        self.rig.bridge.transport.close()  # simulated USB unplug
        self.rig.pump(120)  # past the bridge's 1 s reconnect period
        self.assertFalse(self.rig.service.control.armed)
        self.assertTrue(self.rig.state()["motor"]["transport_connected"])
        self.assertFalse(self.rig.firmware.armed)
        self.assertEqual(self.rig.firmware.outputs, STOPPED)


class WebMotorBackendTests(unittest.TestCase):
    def test_create_app_selects_the_bridge_backend(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="rb", dir="/tmp"))
        self.addCleanup(shutil.rmtree, directory, True)
        app = create_app(
            motor_backend="bridge",
            bridge_command_socket=directory / "cmd.sock",
            bridge_status_socket=directory / "status.sock",
        )
        service = app.state.control_service
        self.addCleanup(service.close)
        self.assertIsInstance(service.backend, BridgeMotorBackend)
        self.assertEqual(service.state()["motor"]["backend"], "bridge")
        self.assertFalse(service.state()["motor"]["healthy"])

    def test_unknown_motor_backend_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_app(motor_backend="serial")

    def test_create_app_selects_the_simulation_only_gazebo_backend(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="rb", dir="/tmp"))
        self.addCleanup(shutil.rmtree, directory, True)
        app = create_app(motor_backend="gazebo", sim_command_socket=directory / "sim.sock")
        service = app.state.control_service
        self.addCleanup(service.close)
        self.assertEqual(service.state()["motor"]["backend"], "gazebo")


    def test_gazebo_autonomy_uses_the_same_runtime_directory_as_ros(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rb", dir="/tmp") as directory:
            with patch.dict("os.environ", {"XDG_RUNTIME_DIR": directory}):
                status = DatagramReceiver(Path(directory) / "autonomy-status.sock")
                service = create_app(motor_backend="gazebo").state.control_service
                try:
                    self.assertTrue((Path(directory) / "autonomy-command.sock").exists())
                    service.tick()
                    self.assertTrue(status.drain())
                finally:
                    service.close()
                    status.close()


if __name__ == "__main__":
    unittest.main()
