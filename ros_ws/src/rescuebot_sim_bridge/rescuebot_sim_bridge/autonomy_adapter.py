"""Adapt Collision Monitor's safe ROS velocity into host autonomy IPC.

This is a simulation-only source adapter.  It subscribes to ``/cmd_vel_safe``
and never publishes directly to Gazebo, opens serial, arms firmware, or mixes
wheels.  The host remains responsible for command priority and final output.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from geometry_msgs.msg import Twist
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from rescuebot.autonomy import AUTONOMY_TIMEOUT_S, AutonomyIntent, AutonomyStatus
from rescuebot.autonomy_ipc import decode_autonomy_status, encode_autonomy_intent, fresh_expiry
from rescuebot.bridge_ipc import DatagramReceiver, DatagramSender
from rescuebot.ros_conversion import ros_to_robot_motion


def _runtime_socket(name: str) -> str:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/rescuebot-{os.getuid()}"))
    return str(runtime / name)


class SimulationAutonomyAdapter(Node):
    """Forward only fresh Collision Monitor output for an active host mission."""

    def __init__(
        self,
        command_socket: str,
        status_socket: str,
        max_vx: float,
        max_vy: float,
        max_wz: float,
    ) -> None:
        super().__init__("rescuebot_sim_autonomy_adapter")
        self._status_receiver = DatagramReceiver(status_socket)
        self._command_sender = DatagramSender(command_socket)
        self._max_vx, self._max_vy, self._max_wz = max_vx, max_vy, max_wz
        self._status = AutonomyStatus(False, None, None)
        self._latest_twist: Twist | None = None
        self._received_at: float | None = None
        self._seq = 0
        self._status_publisher = self.create_publisher(String, "/rescuebot/autonomy_status", 10)
        self.create_subscription(Twist, "/cmd_vel_safe", self._receive_twist, 10)
        self.create_timer(0.05, self._forward)
        self.get_logger().info("subscribing to /cmd_vel_safe; physical motor access is disabled")

    def destroy_node(self) -> bool:
        self._status_receiver.close()
        self._command_sender.close()
        return super().destroy_node()

    def _receive_twist(self, message: Twist) -> None:
        self._latest_twist = message
        self._received_at = time.monotonic()

    def _forward(self) -> None:
        for raw in self._status_receiver.drain():
            try:
                self._status = decode_autonomy_status(raw)
                self._publish_status()
            except ValueError as exc:
                self.get_logger().warning(f"discarded autonomy status: {exc}")

        now = time.monotonic()
        if not self._status.active or self._status.mission is None:
            return
        if self._latest_twist is None or self._received_at is None or now - self._received_at >= AUTONOMY_TIMEOUT_S:
            # Silence is intentionally sent as no command: the host's source
            # expiry disarms rather than allowing an old velocity to persist.
            return
        motion = ros_to_robot_motion(
            self._latest_twist.linear.x,
            self._latest_twist.linear.y,
            self._latest_twist.angular.z,
            max_vx=self._max_vx,
            max_vy=self._max_vy,
            max_wz=self._max_wz,
        )
        self._seq += 1
        intent = AutonomyIntent(
            mission=self._status.mission,
            seq=self._seq,
            expires_at=fresh_expiry(now),
            forward=motion.forward,
            sideways=motion.sideways,
            turn=motion.turn,
        )
        self._command_sender.send(encode_autonomy_intent(intent))

    def _publish_status(self) -> None:
        message = String()
        message.data = json.dumps(
            {"active": self._status.active, "mission": self._status.mission, "reason": self._status.reason},
            separators=(",", ":"),
        )
        self._status_publisher.publish(message)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Send safe ROS autonomy velocity to the simulation host arbiter")
    parser.add_argument("--command-socket", default=os.environ.get("RESCUEBOT_AUTONOMY_COMMAND_SOCKET", _runtime_socket("autonomy-command.sock")))
    parser.add_argument("--status-socket", default=os.environ.get("RESCUEBOT_AUTONOMY_STATUS_SOCKET", _runtime_socket("autonomy-status.sock")))
    parser.add_argument("--max-vx", type=float, default=0.40)
    parser.add_argument("--max-vy", type=float, default=0.40)
    parser.add_argument("--max-wz", type=float, default=1.20)
    args = parser.parse_args(argv)
    if min(args.max_vx, args.max_vy, args.max_wz) <= 0.0:
        parser.error("velocity limits must be positive")

    rclpy.init(args=None)
    node = SimulationAutonomyAdapter(
        args.command_socket, args.status_socket, args.max_vx, args.max_vy, args.max_wz
    )
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
