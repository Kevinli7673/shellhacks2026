"""Own RViz goals and cancel them when the simulation host stops the mission."""

from __future__ import annotations

import json
import math
import time

from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener

from rescuebot.bridge_ipc import DatagramReceiver, DatagramSender
from rescuebot.navigation_ipc import (
    STATUS_TIMEOUT_S, decode_navigation_goal, encode_navigation, navigation_socket,
)


class MissionManager(Node):
    def __init__(self, goal_socket=None, status_socket=None) -> None:
        super().__init__("rescuebot_mission_manager")
        self._active = False
        self._mission = None
        self._last_status = None
        self._generation = 0
        self._pending = False
        self._goal_handle = None
        self._goal_state = "idle"
        self._last_request = None
        self._last_safe_velocity = None
        self._goal_receiver = DatagramReceiver(goal_socket or navigation_socket("navigation-goal.sock"))
        self._status_sender = DatagramSender(status_socket or navigation_socket("navigation-status.sock"))
        self._transforms = Buffer(node=self)
        self._transform_listener = TransformListener(self._transforms, self)
        self._action = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._idle_velocity = self.create_publisher(Twist, "/cmd_vel_nav", 10)
        self._goal_status = self.create_publisher(String, "/rescuebot/navigation_status", 10)
        self.create_subscription(PoseStamped, "/goal_pose", self._goal, 10)
        self.create_subscription(String, "/rescuebot/autonomy_status", self._status, 10)
        self.create_subscription(Twist, "/cmd_vel_safe", self._safe_velocity, 10)
        self.create_timer(0.05, self._tick)

    def destroy_node(self):
        self._goal_receiver.close()
        self._status_sender.close()
        return super().destroy_node()

    def _safe_velocity(self, _message):
        self._last_safe_velocity = time.monotonic()

    def _map_pose(self):
        try:
            transform = self._transforms.lookup_transform("map", "base_link", rclpy.time.Time())
        except TransformException:
            return None
        stamp = transform.header.stamp
        age = self.get_clock().now().nanoseconds / 1e9 - (stamp.sec + stamp.nanosec / 1e9)
        if not -0.1 <= age < 0.5:
            return None
        p, q = transform.transform.translation, transform.transform.rotation
        return {"x": p.x, "y": p.y,
                "yaw": math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y*q.y + q.z*q.z))}

    def _readiness(self, now, pose):
        if self._last_status is None or now - self._last_status >= 0.25:
            return "Waiting for dashboard"
        if not self._action.server_is_ready():
            return "Waiting for Nav2"
        if pose is None:
            return "Waiting for a fresh SLAM pose"
        if self._last_safe_velocity is None or now - self._last_safe_velocity >= 0.25:
            return "Waiting for Collision Monitor"
        return "Ready"

    def _dashboard_goals(self, now, pose, ready):
        for raw in self._goal_receiver.drain():
            try:
                record = decode_navigation_goal(raw, now)
            except (ValueError, TypeError, KeyError):
                continue
            if (not ready or not self._active or record["mission"] != self._mission
                    or record["request_id"] == self._last_request):
                continue
            self._last_request = record["request_id"]
            goal = PoseStamped()
            goal.header.frame_id = "map"
            goal.header.stamp = self.get_clock().now().to_msg()
            forward, right, yaw = record["forward"], record["right"], pose["yaw"]
            # Dashboard axes: forward/right; ROS body axes: forward/left.
            goal.pose.position.x = pose["x"] + math.cos(yaw)*forward + math.sin(yaw)*right
            goal.pose.position.y = pose["y"] + math.sin(yaw)*forward - math.cos(yaw)*right
            goal.pose.orientation.z = math.sin(yaw / 2)
            goal.pose.orientation.w = math.cos(yaw / 2)
            self._goal(goal)

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
            self._last_request = None
            if active:
                self._goal_state = "idle"
        self._active, self._mission = active, mission
        self._last_status = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        host_fresh = self._last_status is not None and now - self._last_status < 0.25
        if not host_fresh:
            if self._active:
                self._cancel_goal()
                self._active = False
        if host_fresh and not self._pending and self._goal_handle is None:
            # Nav2 is silent before/after goals. Keep an idle mission alive with
            # zeros through the same smoother and Collision Monitor. Never
            # replace missing controller output while a goal is in progress.
            self._idle_velocity.publish(Twist())
        pose = self._map_pose()
        reason = self._readiness(now, pose)
        self._dashboard_goals(now, pose, reason == "Ready")
        record = {
            "mission": self._mission, "active": self._active,
            "goal_state": self._goal_state,
            "ready": reason == "Ready", "reason": reason, "pose": pose,
            "request_id": self._last_request, "expires_at": now + STATUS_TIMEOUT_S,
        }
        self._goal_status.publish(String(data=json.dumps(record)))
        self._status_sender.send(encode_navigation(record))

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
