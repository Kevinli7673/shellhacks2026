"""Provide ``odom -> base_link`` TF from simulated Gazebo odometry."""

from __future__ import annotations

from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from tf2_ros import TransformBroadcaster


class OdomTfBridge(Node):
    def __init__(self) -> None:
        super().__init__("rescuebot_sim_odom_tf")
        self._broadcaster = TransformBroadcaster(self)
        self.create_subscription(Odometry, "/odom", self._odom, 20)

    def _odom(self, message: Odometry) -> None:
        transform = TransformStamped()
        transform.header.stamp = message.header.stamp
        transform.header.frame_id = message.header.frame_id or "odom"
        transform.child_frame_id = message.child_frame_id or "base_link"
        transform.transform.translation.x = message.pose.pose.position.x
        transform.transform.translation.y = message.pose.pose.position.y
        transform.transform.translation.z = message.pose.pose.position.z
        transform.transform.rotation = message.pose.pose.orientation
        self._broadcaster.sendTransform(transform)


def main() -> None:
    rclpy.init()
    node = OdomTfBridge()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
