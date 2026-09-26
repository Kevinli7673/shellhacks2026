import unittest

from rescuebot.control import DriveIntent, ManualControl
from rescuebot.service import RobotControlService


class KeyboardIntentTests(unittest.TestCase):
    def test_each_keyboard_direction_matches_the_approved_axis_convention(self) -> None:
        self.assertEqual(DriveIntent.from_keys({"KeyW"}), DriveIntent(forward=1))
        self.assertEqual(DriveIntent.from_keys({"KeyS"}), DriveIntent(forward=-1))
        self.assertEqual(DriveIntent.from_keys({"KeyD"}), DriveIntent(sideways=1))
        self.assertEqual(DriveIntent.from_keys({"KeyA"}), DriveIntent(sideways=-1))
        self.assertEqual(DriveIntent.from_keys({"ArrowRight"}), DriveIntent(turn=1))
        self.assertEqual(DriveIntent.from_keys({"ArrowLeft"}), DriveIntent(turn=-1))

    def test_opposing_inputs_cancel(self) -> None:
        self.assertEqual(
            DriveIntent.from_keys({"KeyW", "KeyS", "KeyA", "KeyD", "ArrowLeft", "ArrowRight"}),
            DriveIntent(),
        )

    def test_combined_movement_preserves_all_requested_axes(self) -> None:
        self.assertEqual(
            DriveIntent.from_keys({"KeyW", "KeyA", "ArrowRight"}),
            DriveIntent(forward=1, sideways=-1, turn=1),
        )


class ManualSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = ManualControl(pwm_ceiling=200, input_timeout_s=0.250)
        self.session = "browser-one"
        self.assertTrue(self.control.claim(self.session))

    def test_only_one_browser_can_claim_control(self) -> None:
        self.assertFalse(self.control.claim("browser-two"))

    def test_enable_requires_explicit_action_and_released_keys(self) -> None:
        self.assertTrue(self.control.set_keys(self.session, {"KeyW"}, now=1.0))
        self.assertFalse(self.control.enable(self.session, now=1.0))
        self.assertEqual(self.control.snapshot(now=1.0).fault, "release_keys_before_enable")

        self.assertTrue(self.control.set_keys(self.session, set(), now=1.1))
        self.assertTrue(self.control.enable(self.session, now=1.1))

    def test_speed_adjustment_alone_cannot_move_the_robot(self) -> None:
        self.assertTrue(self.control.enable(self.session, now=1.0))
        self.assertTrue(self.control.adjust_speed(self.session, 10))
        snapshot = self.control.snapshot(now=1.1)
        self.assertEqual(snapshot.speed_percent, 40)
        self.assertTrue(snapshot.intent.is_stopped)
        self.assertEqual(snapshot.wheels.as_dict(), {"fl": 0, "fr": 0, "rl": 0, "rr": 0})

    def test_speed_adjustment_uses_ten_percent_steps_and_stays_in_bounds(self) -> None:
        self.assertTrue(self.control.adjust_speed(self.session, -10))
        self.assertTrue(self.control.adjust_speed(self.session, -10))
        self.assertEqual(self.control.speed_percent, 10)
        self.assertTrue(self.control.adjust_speed(self.session, -10))
        self.assertEqual(self.control.speed_percent, 10)

        for _ in range(10):
            self.assertTrue(self.control.adjust_speed(self.session, 10))
        self.assertEqual(self.control.speed_percent, 100)
        self.assertTrue(self.control.adjust_speed(self.session, 10))
        self.assertEqual(self.control.speed_percent, 100)
        self.assertFalse(self.control.adjust_speed(self.session, 5))

    def test_release_stops_motion_without_releasing_control(self) -> None:
        self.assertTrue(self.control.enable(self.session, now=1.0))
        self.assertTrue(self.control.set_keys(self.session, {"KeyW", "KeyD"}, now=1.1))
        self.assertNotEqual(self.control.snapshot(now=1.2).wheels.as_dict(), {"fl": 0, "fr": 0, "rl": 0, "rr": 0})

        self.assertTrue(self.control.set_keys(self.session, set(), now=1.21))
        released = self.control.snapshot(now=1.22)
        self.assertTrue(released.armed)
        self.assertEqual(released.wheels.as_dict(), {"fl": 0, "fr": 0, "rl": 0, "rr": 0})

    def test_input_timeout_disarms_and_cannot_resume_old_movement(self) -> None:
        self.assertTrue(self.control.enable(self.session, now=1.0))
        self.assertTrue(self.control.set_keys(self.session, {"KeyW"}, now=1.01))
        self.assertTrue(self.control.snapshot(now=1.2).armed)

        timed_out = self.control.snapshot(now=1.27)
        self.assertFalse(timed_out.armed)
        self.assertEqual(timed_out.fault, "browser_timeout")
        self.assertEqual(timed_out.wheels.as_dict(), {"fl": 0, "fr": 0, "rl": 0, "rr": 0})

        self.assertTrue(self.control.set_keys(self.session, {"KeyW"}, now=1.28))
        self.assertFalse(self.control.snapshot(now=1.29).armed)

    def test_stop_and_disconnect_require_explicit_new_enable(self) -> None:
        self.assertTrue(self.control.enable(self.session, now=1.0))
        self.assertTrue(self.control.set_keys(self.session, {"KeyW"}, now=1.1))
        self.control.stop()
        self.assertFalse(self.control.snapshot(now=1.11).armed)

        self.control.disconnect(self.session)
        self.assertIsNone(self.control.owner_session)
        self.assertFalse(self.control.enable("new-browser", now=1.12))
        self.assertTrue(self.control.claim("new-browser"))
        self.assertTrue(self.control.enable("new-browser", now=1.12))

    def test_invalid_key_packet_stops_and_disarms(self) -> None:
        self.assertTrue(self.control.enable(self.session, now=1.0))
        self.assertFalse(self.control.set_keys(self.session, {"KeyW", "Space"}, now=1.1))
        stopped = self.control.snapshot(now=1.1)
        self.assertFalse(stopped.armed)
        self.assertEqual(stopped.fault, "invalid_keys")

    def test_non_owner_cannot_set_keys_or_adjust_speed(self) -> None:
        self.assertFalse(self.control.set_keys("browser-two", {"KeyW"}, now=1.0))
        self.assertFalse(self.control.adjust_speed("browser-two", 10))


class MockBackendTests(unittest.TestCase):
    def test_mock_backend_uses_the_same_mixing_and_stops_after_timeout(self) -> None:
        service = RobotControlService(ManualControl(pwm_ceiling=100, input_timeout_s=0.250))
        self.assertTrue(service.claim("browser-one"))
        self.assertTrue(service.enable("browser-one", now=1.0))
        self.assertTrue(service.keys("browser-one", ["KeyD"], now=1.01))
        self.assertEqual(service.backend.wheels.as_dict(), {"fl": 30, "fr": -30, "rl": -30, "rr": 30})

        service.tick(now=1.27)
        self.assertEqual(service.backend.wheels.as_dict(), {"fl": 0, "fr": 0, "rl": 0, "rr": 0})
        self.assertEqual(service.backend.last_reason, "browser_timeout")
