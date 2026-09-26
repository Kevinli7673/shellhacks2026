"""Publish Gazebo ``cmd_vel`` from the selected host simulation command.

This node deliberately receives only simulation IPC.  It cannot use the
ESP32 serial transport, firmware protocol, or physical motor shield.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

from geometry_msgs.msg import Twist
import rclpy
from rclpy.node import Node

from rescuebot.bridge_ipc import DatagramReceiver
from rescuebot.ros_conversion import robot_to_ros_velocity
from rescuebot.simulation_ipc import SimulationCommand, SimulationIpcError


def _default_socket() -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/rescuebot-{os.getuid()}")
    return str(Path(runtime_dir) / "gazebo-command.sock")


class SimulationCommandBridge(Node):
    """Drain newest-only IPC commands and publish a zero-safe ROS Twist."""

    def __init__(self, socket_path: str, max_vx: float, max_vy: float, max_wz: float) -> None:
        super().__init__("rescuebot_sim_command_bridge")
        self._receiver = DatagramReceiver(socket_path)
        self._max_vx = max_vx
        self._max_vy = max_vy
        self._max_wz = max_wz
        self._latest: SimulationCommand | None = None
        self._last_seq = 0
        self._publisher = self.create_publisher(Twist, "/cmd_vel", 10)
        self._timer = self.create_timer(0.05, self._publish_latest)
        self.get_logger().info(f"listening for simulation commands at {socket_path}")

    def destroy_node(self) -> bool:
        self._receiver.close()
        return super().destroy_node()

    def _publish_latest(self) -> None:
        for raw in self._receiver.drain():
            try:
                command = SimulationCommand.decode(raw)
            except SimulationIpcError as exc:
                self.get_logger().warning(f"discarded simulation command: {exc}")
                continue
            if command.seq <= self._last_seq:
                continue
            self._last_seq = command.seq
            self._latest = command

        message = Twist()
        command = self._latest
        if command is not None and command.armed and command.is_fresh(time.monotonic()):
            velocity = robot_to_ros_velocity(
                command.forward,
                command.sideways,
                command.turn,
                max_vx=self._max_vx,
                max_vy=self._max_vy,
                max_wz=self._max_wz,
            )
            message.linear.x = velocity.linear_x
            message.linear.y = velocity.linear_y
            message.angular.z = velocity.angular_z
        self._publisher.publish(message)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bridge selected Rescuebot simulator commands to /cmd_vel")
    parser.add_argument("--socket", default=os.environ.get("RESCUEBOT_SIM_COMMAND_SOCKET", _default_socket()))
    parser.add_argument("--max-vx", type=float, default=0.40)
    parser.add_argument("--max-vy", type=float, default=0.40)
    parser.add_argument("--max-wz", type=float, default=1.20)
    args = parser.parse_args(argv)
    if min(args.max_vx, args.max_vy, args.max_wz) <= 0.0:
        parser.error("velocity limits must be positive")

    rclpy.init(args=None)
    node = SimulationCommandBridge(args.socket, args.max_vx, args.max_vy, args.max_wz)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
