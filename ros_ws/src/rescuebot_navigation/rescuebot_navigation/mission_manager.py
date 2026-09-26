"""Mission-owned Nav2 goals with immediate cancellation on host override.

RViz publishes a manually selected PoseStamped on ``/goal_pose``.  This node
owns the resulting NavigateToPose action, allowing the host's autonomy status
to cancel it.  It neither publishes velocity nor accesses serial hardware.
"""

from __future__ import annotations

import json
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String


class MissionManager(Node):
    def __init__(self) -> None:
        super().__init__("rescuebot_mission_manager")
        self._active = False
        self._goal_handle = None
        self._action = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self.create_subscription(PoseStamped, "/goal_pose", self._goal, 10)
        self.create_subscription(String, "/rescuebot/autonomy_status", self._status, 10)

    def _status(self, message: String) -> None:
        # The adapter publishes a compact JSON record. Only the active flag is
        # relevant here; host command priority remains in RobotControlService.
        try:
            record = json.loads(message.data)
            active = bool(record["active"])
        except (KeyError, TypeError, json.JSONDecodeError):
            self.get_logger().warning("discarded malformed autonomy status")
            return
        if self._active and not active:
            self._cancel_goal()
        self._active = active

    def _goal(self, pose: PoseStamped) -> None:
        if not self._active:
            self.get_logger().warning("ignored goal_pose until the operator starts autonomy")
            return
        if not self._action.wait_for_server(timeout_sec=1.0):
            self.get_logger().error("NavigateToPose action server is unavailable")
            return
        self._cancel_goal()
        goal = NavigateToPose.Goal()
        goal.pose = pose
        future = self._action.send_goal_async(goal)
        future.add_done_callback(self._goal_response)

    def _goal_response(self, future) -> None:
        try:
            handle = future.result()
        except Exception as exc:
            self.get_logger().error(f"goal request failed: {exc}")
            return
        if not handle.accepted:
            self.get_logger().warning("navigation goal was rejected")
            return
        self._goal_handle = handle
        handle.get_result_async().add_done_callback(self._goal_finished)

    def _goal_finished(self, _future) -> None:
        self._goal_handle = None

    def _cancel_goal(self) -> None:
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()
            self._goal_handle = None


def main() -> None:
    rclpy.init()
    node = MissionManager()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
