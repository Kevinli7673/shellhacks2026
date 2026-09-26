"""Own RViz goals and cancel them when the simulation host stops the mission."""

from __future__ import annotations

import json
import time

from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String


class MissionManager(Node):
    def __init__(self) -> None:
        super().__init__("rescuebot_mission_manager")
        self._active = False
        self._mission = None
        self._last_status = None
        self._generation = 0
        self._pending = False
        self._goal_handle = None
        self._goal_state = "idle"
        self._action = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._idle_velocity = self.create_publisher(Twist, "/cmd_vel_nav", 10)
        self._goal_status = self.create_publisher(String, "/rescuebot/navigation_status", 10)
        self.create_subscription(PoseStamped, "/goal_pose", self._goal, 10)
        self.create_subscription(String, "/rescuebot/autonomy_status", self._status, 10)
        self.create_timer(0.05, self._tick)

    def _status(self, message: String) -> None:
        try:
            record = json.loads(message.data)
            active, mission = record["active"], record["mission"]
            if not isinstance(active, bool) or (active and not isinstance(mission, str)):
                raise ValueError("invalid mission status")
        except (KeyError, TypeError, ValueError):
            self.get_logger().warning("discarded malformed autonomy status")
            return
        if self._mission != mission or (self._active and not active):
            self._cancel_goal()
        self._active, self._mission = active, mission
        self._last_status = time.monotonic()

    def _tick(self) -> None:
        if self._last_status is None or time.monotonic() - self._last_status >= 0.25:
            if self._active:
                self._cancel_goal()
                self._active = False
            return
        if not self._pending and self._goal_handle is None:
            # Nav2 is silent before/after goals. Keep an idle mission alive with
            # zeros through the same smoother and Collision Monitor. Never
            # replace missing controller output while a goal is in progress.
            self._idle_velocity.publish(Twist())
        self._goal_status.publish(String(data=json.dumps({
            "mission": self._mission, "active": self._active,
            "goal_state": self._goal_state,
        })))

    def _goal(self, pose: PoseStamped) -> None:
        if not self._active or self._last_status is None or time.monotonic() - self._last_status >= 0.25:
            self.get_logger().warning("ignored goal_pose until the operator starts autonomy")
            return
        if self._pending or self._goal_handle is not None:
            self.get_logger().warning("stop the current goal before selecting another")
            return
        if not self._action.server_is_ready():
            self.get_logger().error("NavigateToPose action server is unavailable")
            return
        self._generation += 1
        generation = self._generation
        self._pending = True
        self._goal_state = "pending"
        goal = NavigateToPose.Goal()
        goal.pose = pose
        future = self._action.send_goal_async(goal)
        future.add_done_callback(lambda result: self._goal_response(result, generation))

    def _goal_response(self, future, generation) -> None:
        try:
            handle = future.result()
        except Exception as exc:
            if generation == self._generation:
                self._pending = False
                self._goal_state = "failed"
            self.get_logger().error(f"goal request failed: {exc}")
            return
        # An asynchronous acceptance can arrive after Stop/manual override.
        if generation != self._generation or not self._active:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        self._pending = False
        if not handle.accepted:
            self._goal_state = "rejected"
            return
        self._goal_handle = handle
        self._goal_state = "executing"
        handle.get_result_async().add_done_callback(
            lambda result: self._goal_finished(result, generation))

    def _goal_finished(self, future, generation) -> None:
        if generation != self._generation:
            return
        self._goal_handle = None
        try:
            status = future.result().status
            self._goal_state = {4: "succeeded", 5: "canceled", 6: "aborted"}.get(status, "failed")
        except Exception:
            self._goal_state = "failed"
        self.get_logger().info(f"navigation goal {self._goal_state}")

    def _cancel_goal(self) -> None:
        self._generation += 1
        self._pending = False
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()
            self._goal_handle = None
        self._goal_state = "canceled"


def main() -> None:
    rclpy.init()
    node = MissionManager()
    try:
        rclpy.spin(node)
    finally:
        node._cancel_goal()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
