"""Exercise asynchronous goal cancellation and idle-source expiry."""

from concurrent.futures import Future
import json
import math
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from geometry_msgs.msg import PoseStamped
import pytest
import rclpy
from std_msgs.msg import String

from rescuebot_navigation.mission_manager import MissionManager
from rescuebot.navigation_ipc import encode_navigation
from rescuebot.bridge_ipc import DatagramSender


@pytest.fixture
def manager(tmp_path):
    rclpy.init()
    with patch("rescuebot_navigation.mission_manager.ActionClient"):
        node = MissionManager(tmp_path / "goals.sock", tmp_path / "status.sock")
    node._idle_velocity = Mock()
    node._goal_status = Mock()
    node._action.server_is_ready.return_value = True
    try:
        yield node
    finally:
        node.destroy_node()
        rclpy.shutdown()


def status(node, active=True, mission="test"):
    node._status(String(data=json.dumps({"active": active, "mission": mission})))


def accepted_handle():
    handle = Mock(accepted=True)
    handle.get_result_async.return_value = Future()
    return handle


def test_stop_cancels_late_acceptance(manager):
    status(manager)
    response = Future()
    manager._action.send_goal_async.return_value = response
    manager._goal(PoseStamped())
    status(manager, False, None)
    handle = accepted_handle()
    response.set_result(handle)
    handle.cancel_goal_async.assert_called_once()
    assert manager._goal_handle is None


def test_old_result_cannot_clear_a_new_goal(manager):
    status(manager)
    first = Future()
    manager._action.send_goal_async.return_value = first
    manager._goal(PoseStamped())
    old = accepted_handle()
    first.set_result(old)
    status(manager, False, None)
    status(manager, True, "next")
    second = Future()
    manager._action.send_goal_async.return_value = second
    manager._goal(PoseStamped())
    new = accepted_handle()
    second.set_result(new)
    old.get_result_async.return_value.set_result(SimpleNamespace(status=5))
    assert manager._goal_handle is new
    new.get_result_async.return_value.set_result(SimpleNamespace(status=4))
    assert manager._goal_state == "succeeded"


def test_idle_zeros_do_not_mask_controller_loss(manager):
    status(manager)
    manager._tick()
    manager._idle_velocity.publish.assert_called_once()
    message = manager._idle_velocity.publish.call_args.args[0]
    assert message.linear.x == message.linear.y == message.angular.z == 0.0
    manager._idle_velocity.reset_mock()
    manager._action.send_goal_async.return_value = Future()
    manager._goal(PoseStamped())
    manager._tick()
    manager._idle_velocity.publish.assert_not_called()
    manager._last_status -= 0.30
    manager._tick()
    assert not manager._active
    manager._idle_velocity.publish.assert_not_called()


def test_dashboard_goal_uses_current_pose_and_right_axis_once(manager):
    status(manager)
    manager._action.send_goal_async.return_value = Future()
    now = time.monotonic()
    record = {"mission": "test", "request_id": "one", "expires_at": now + 0.25, "forward": 0.5, "right": 0.2}
    sender = DatagramSender(manager._goal_receiver.path)
    try:
        sender.send(encode_navigation(record))
        manager._dashboard_goals(now, {"x": 1.0, "y": 2.0, "yaw": math.pi/2}, True)
        goal = manager._action.send_goal_async.call_args.args[0].pose
        assert goal.header.frame_id == "map"
        assert goal.pose.position.x == pytest.approx(1.2)
        assert goal.pose.position.y == pytest.approx(2.5)
        heading = math.atan2(0.5, 0.2)
        assert goal.pose.orientation.z == pytest.approx(math.sin(heading / 2))
        assert goal.pose.orientation.w == pytest.approx(math.cos(heading / 2))
        sender.send(encode_navigation(record))
        manager._dashboard_goals(now, {"x": 1.0, "y": 2.0, "yaw": 0}, True)
        manager._action.send_goal_async.assert_called_once()
    finally:
        sender.close()


def test_dashboard_goal_cannot_survive_stop_mission_change_or_expiry(manager):
    status(manager)
    now = time.monotonic()
    sender = DatagramSender(manager._goal_receiver.path)
    pose = {"x": 0, "y": 0, "yaw": 0}
    record = {"mission": "test", "request_id": "one", "expires_at": now + 0.25, "forward": 0.5, "right": 0}
    try:
        status(manager, False, None)
        sender.send(encode_navigation(record))
        manager._dashboard_goals(now, pose, True)
        status(manager, True, "next")
        sender.send(encode_navigation(record))
        manager._dashboard_goals(now, pose, True)
        sender.send(encode_navigation({**record, "mission": "next", "expires_at": now}))
        manager._dashboard_goals(now, pose, True)
        sender.send(encode_navigation({**record, "mission": "next"}))
        manager._dashboard_goals(now, pose, False)
        manager._action.send_goal_async.assert_not_called()
    finally:
        sender.close()


@pytest.mark.parametrize("remaining, accepted", [(0.25, True), (-0.001, False)])
def test_goal_deadline_is_checked_when_received_not_when_tick_started(manager, remaining, accepted):
    status(manager)
    manager._action.send_goal_async.return_value = Future()
    now = time.monotonic()
    record = {"mission": "test", "request_id": "during-tick", "expires_at": now + remaining,
              "forward": .5, "right": 0}
    sender = DatagramSender(manager._goal_receiver.path)
    try:
        sender.send(encode_navigation(record))
        # TF/readiness work can overlap a new arrival or the expiry boundary.
        manager._dashboard_goals(now - .02, {"x": 0, "y": 0, "yaw": 0}, True)
        assert manager._action.send_goal_async.called == accepted
    finally:
        sender.close()


def test_local_host_status_expiry_logs_measured_age_without_idle_masking(manager):
    status(manager)
    logger = Mock()
    manager.get_logger = Mock(return_value=logger)
    manager._last_status = time.monotonic() - .30
    manager._tick()
    message = logger.warning.call_args.args[0]
    assert "host status stale" in message and "limit=0.250s" in message
    assert float(message.split("age=", 1)[1].split("s", 1)[0]) >= .30
    assert "mission='test'" in message
    assert not manager._active and manager._goal_state == "canceled"
    manager._idle_velocity.publish.assert_not_called()


def test_host_cancellation_logs_original_reason_and_mission(manager):
    status(manager)
    logger = Mock()
    manager.get_logger = Mock(return_value=logger)
    manager._status(String(data=json.dumps({
        "active": False, "mission": None, "reason": "autonomy_timeout",
    })))
    message = logger.warning.call_args.args[0]
    assert "after host status" in message
    assert "reason='autonomy_timeout'" in message
    assert "mission=None" in message and "previous_mission='test'" in message
    assert not manager._active and manager._goal_state == "canceled"
