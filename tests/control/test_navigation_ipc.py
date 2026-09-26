import tempfile
import time
import unittest
from pathlib import Path

from rescuebot.bridge_ipc import DatagramReceiver, DatagramSender
from rescuebot.gazebo_backend import GazeboMotorBackend
from rescuebot.navigation_ipc import (
    NavigationHostEndpoint, decode_navigation_goal, encode_navigation, goal_offsets,
)
from rescuebot.service import RobotControlService


class NavigationDashboardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.goals = DatagramReceiver(root / "goal.sock")
        self.telemetry = DatagramSender(root / "status.sock")
        self.motors = DatagramReceiver(root / "sim.sock")
        self.endpoint = NavigationHostEndpoint(root / "goal.sock", root / "status.sock")
        self.service = RobotControlService(
            backend=GazeboMotorBackend(root / "sim.sock"), allow_autonomy=True,
            navigation_endpoint=self.endpoint,
        )
        for resource in (self.goals, self.telemetry, self.motors, self.service):
            self.addCleanup(resource.close)
        self.service.claim("owner")
        self.service.enable("owner")

    def publish(self, *, mission=None, expiry=None, **changes):
        self.telemetry.send(encode_navigation({
            "ready": True, "reason": "Ready", "mission": mission,
            "active": mission is not None, "goal_state": "idle", "request_id": None,
            "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "expires_at": expiry if expiry is not None else time.monotonic() + 0.5,
            **changes,
        }))

    def test_readiness_expires_and_cannot_be_replaced_by_old_or_malformed_status(self):
        self.assertIsNone(self.service.start_autonomy("owner"))
        now = time.monotonic()
        self.publish(expiry=now + 0.4)
        self.assertTrue(self.endpoint.state(now)["ready"])
        self.publish(expiry=now + 0.3, ready=False)
        self.telemetry.send(b'{"ready":true}')
        self.assertTrue(self.endpoint.state(now)["ready"])
        self.assertFalse(self.endpoint.state(now + 0.4)["ready"])

    def test_goal_needs_owner_current_mission_and_acknowledgment_before_another(self):
        self.publish()
        mission = self.service.start_autonomy("owner")
        self.assertIsNotNone(mission)
        self.assertFalse(self.service.navigation_goal("owner", 0.5, 0))
        self.publish(mission=mission)
        self.assertFalse(self.service.navigation_goal("viewer", 0.5, 0))
        self.assertTrue(self.service.navigation_goal("owner", 0.5, 0))
        self.assertFalse(self.service.navigation_goal("owner", 0.5, 0))
        record = decode_navigation_goal(self.goals.drain()[0], time.monotonic())
        self.assertEqual(record["mission"], mission)
        self.publish(mission=mission, request_id=record["request_id"], goal_state="executing")
        self.assertFalse(self.service.navigation_goal("owner", 0, 0.5))
        self.service.stop()
        self.assertFalse(self.service.navigation_goal("owner", 0, 0.5))
        self.assertEqual(self.goals.drain(), [])

    def test_invalid_expired_and_unbounded_goal_inputs_fail_closed(self):
        for forward, right in [(True, 0), (float("nan"), 0), (float("inf"), 0), (10**500, 0), (0, 0), (2, 2), ("1", 0)]:
            with self.assertRaises(ValueError):
                goal_offsets(forward, right)
        record = {"mission": "test", "request_id": "one", "forward": 0.5, "right": 0, "expires_at": 1.0}
        with self.assertRaises(ValueError):
            decode_navigation_goal(encode_navigation(record), 1.0)
        with self.assertRaises(ValueError):
            decode_navigation_goal(encode_navigation({**record, "expires_at": 100.0}), 1.0)

    def test_manual_backend_keeps_navigation_unavailable_even_with_endpoint(self):
        service = RobotControlService(allow_autonomy=True, navigation_endpoint=self.endpoint)
        service.claim("owner")
        service.enable("owner")
        self.assertIsNone(service.start_autonomy("owner"))
        self.assertFalse(service.navigation_goal("owner", 0.5, 0))
        self.assertNotIn("navigation", service.state()["autonomy"])
        self.assertEqual(self.goals.drain(), [])
