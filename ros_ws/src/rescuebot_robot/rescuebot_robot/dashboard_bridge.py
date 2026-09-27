"""Publish the physical robot's LiDAR and IMU heading to ROS 2, read-only.

The running dashboard already owns the LiDAR (rescuebot.lidar_scan) and the
ESP32 link (via rescuebot.motor_bridge). This node only polls its HTTP API:

- GET /api/lidar -> sensor_msgs/LaserScan on /scan (frame "laser")
- GET /api/state -> motor.imu.heading -> odom -> base_link TF and /odom
- GET /api/state -> camera person detections -> /rescuebot/person_detections
  (JSON: bearing, confidence, LiDAR range) and /rescuebot/people (PoseArray
  in base_link, only people with a LiDAR range at their bearing)

The robot has no wheel encoders, so odometry carries heading only (x = y = 0);
slam_toolbox's scan matcher finds the translation. This node has no way to
send drive commands: it never opens a WebSocket, a serial port, or the bridge
sockets.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request

from geometry_msgs.msg import Pose, PoseArray, TransformStamped
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

from .conversions import (
    BIN_COUNT,
    SCAN_ANGLE_INCREMENT,
    SCAN_ANGLE_MIN,
    bearing_range_to_xy,
    bins_to_ranges,
    heading_to_yaw,
    range_at_bearing,
    x_to_bearing,
    yaw_to_quaternion,
)

SCAN_PERIOD_S = 0.1  # the C1 spins at ~10 Hz


class DashboardBridge(Node):
    def __init__(self) -> None:
        super().__init__("rescuebot_dashboard_bridge")
        self.url = self.declare_parameter("dashboard_url", "http://127.0.0.1:8000").value.rstrip("/")
        self.poll_hz = float(self.declare_parameter("poll_hz", 10.0).value)
        self.use_imu = bool(self.declare_parameter("use_imu", True).value)
        self.range_min = float(self.declare_parameter("range_min", 0.08).value)
        self.range_max = float(self.declare_parameter("range_max", 12.0).value)
        self.base_frame = self.declare_parameter("base_frame", "base_link").value
        self.odom_frame = self.declare_parameter("odom_frame", "odom").value
        self.laser_frame = self.declare_parameter("laser_frame", "laser").value
        laser_x = float(self.declare_parameter("laser_x", 0.0).value)
        laser_y = float(self.declare_parameter("laser_y", 0.0).value)
        laser_z = float(self.declare_parameter("laser_z", 0.15).value)

        self.scan_pub = self.create_publisher(LaserScan, "scan", qos_profile_sensor_data)
        self.odom_pub = self.create_publisher(Odometry, "odom", 10)
        self.detections_pub = self.create_publisher(String, "rescuebot/person_detections", 10)
        self.people_pub = self.create_publisher(PoseArray, "rescuebot/people", 10)
        self.tf = TransformBroadcaster(self)
        self.static_tf = StaticTransformBroadcaster(self)
        # The dashboard's bins are already in robot coordinates (lidar_scan --offset).
        self.static_tf.sendTransform(self._transform(
            self.get_clock().now(), self.base_frame, self.laser_frame, laser_x, laser_y, laser_z, 0.0))

        self._last_bins: list[int] | None = None
        self._had_people = False
        self._heading_ref: float | None = None
        self._yaw = 0.0
        self._warned: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dashboard-poll", daemon=True)
        self._thread.start()
        self.get_logger().info(f"Reading LiDAR and IMU from {self.url} (read-only, no drive commands).")

    # -- HTTP ------------------------------------------------------------------

    def _get(self, path: str) -> dict | None:
        try:
            with urllib.request.urlopen(self.url + path, timeout=0.5) as response:
                data = json.load(response)
            self._warned.discard(path)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            if path not in self._warned:
                self._warned.add(path)
                self.get_logger().warning(f"GET {path} failed ({exc}); retrying quietly.")
            return None

    def _run(self) -> None:
        period = 1.0 / max(self.poll_hz, 1.0)
        while not self._stop.is_set():
            started = time.monotonic()
            self._poll_state()
            self._poll_lidar()
            self._stop.wait(max(0.0, period - (time.monotonic() - started)))

    # -- IMU / odometry --------------------------------------------------------

    def _poll_state(self) -> None:
        now = self.get_clock().now()
        state = self._get("/api/state") if self.use_imu else None
        imu = ((state or {}).get("motor") or {}).get("imu") or {}
        heading = imu.get("heading")
        if imu.get("available") and isinstance(heading, (int, float)) and math.isfinite(heading):
            if self._heading_ref is None:
                self._heading_ref = float(heading)
            self._yaw = heading_to_yaw(float(heading), self._heading_ref)
        # Keep publishing (with the last yaw) so TF never goes stale.
        self.tf.sendTransform(self._transform(now, self.odom_frame, self.base_frame, 0.0, 0.0, 0.0, self._yaw))
        odom = Odometry()
        odom.header.stamp = now.to_msg()
        odom.header.frame_id = self.odom_frame
        odom.child_frame_id = self.base_frame
        qx, qy, qz, qw = yaw_to_quaternion(self._yaw)
        odom.pose.pose.orientation.x, odom.pose.pose.orientation.y = qx, qy
        odom.pose.pose.orientation.z, odom.pose.pose.orientation.w = qz, qw
        # Position is unknown (no encoders): very large covariance on x/y.
        odom.pose.covariance[0] = odom.pose.covariance[7] = 1e6
        odom.pose.covariance[35] = 0.05
        self.odom_pub.publish(odom)
        self._publish_people(now, (state or {}).get("camera") or {})

    # -- camera person detections ----------------------------------------------

    def _publish_people(self, now, camera: dict) -> None:
        detections = camera.get("detections") if camera.get("status") == "online" else []
        people = []
        poses = PoseArray()
        poses.header.stamp = now.to_msg()
        poses.header.frame_id = self.base_frame
        for det in detections or []:
            bbox = det.get("bbox") if isinstance(det, dict) else None
            if det.get("label") != "person" or not isinstance(bbox, dict):
                continue
            try:
                bearing = x_to_bearing(float(bbox["x"]) + float(bbox["width"]) / 2)
            except (KeyError, TypeError, ValueError):
                continue
            distance = range_at_bearing(self._last_bins, bearing, range_max_m=self.range_max)
            people.append({"bearing_deg": round(bearing, 1), "confidence": det.get("confidence"),
                           "range_m": None if distance is None else round(distance, 2)})
            if distance is not None:
                x, y = bearing_range_to_xy(bearing, distance)
                pose = Pose()
                pose.position.x, pose.position.y = x, y
                pose.orientation.w = 1.0
                poses.poses.append(pose)
        if not people and not self._had_people:
            return  # stay quiet while nobody is in view
        self._had_people = bool(people)
        self.detections_pub.publish(String(data=json.dumps({"people": people})))
        self.people_pub.publish(poses)

    # -- LiDAR -----------------------------------------------------------------

    def _poll_lidar(self) -> None:
        received = self.get_clock().now()
        lidar = self._get("/api/lidar")
        if not lidar or lidar.get("status") != "online":
            return
        bins = lidar.get("bins")
        if not isinstance(bins, list) or len(bins) != BIN_COUNT or bins == self._last_bins:
            return
        self._last_bins = bins
        age_s = max(0.0, float(lidar.get("age_ms") or 0) / 1000.0)
        stamp = Time(nanoseconds=received.nanoseconds - int(age_s * 1e9), clock_type=received.clock_type)
        scan = LaserScan()
        scan.header.stamp = stamp.to_msg()
        scan.header.frame_id = self.laser_frame
        scan.angle_min = SCAN_ANGLE_MIN
        scan.angle_increment = SCAN_ANGLE_INCREMENT
        scan.angle_max = SCAN_ANGLE_MIN + SCAN_ANGLE_INCREMENT * (BIN_COUNT - 1)
        scan.scan_time = SCAN_PERIOD_S
        scan.time_increment = 0.0
        scan.range_min = self.range_min
        scan.range_max = self.range_max
        scan.ranges = bins_to_ranges(bins, self.range_min, self.range_max)
        self.scan_pub.publish(scan)

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _transform(stamp, parent: str, child: str, x: float, y: float, z: float, yaw: float) -> TransformStamped:
        t = TransformStamped()
        t.header.stamp = stamp.to_msg()
        t.header.frame_id = parent
        t.child_frame_id = child
        t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = x, y, z
        qx, qy, qz, qw = yaw_to_quaternion(yaw)
        t.transform.rotation.x, t.transform.rotation.y = qx, qy
        t.transform.rotation.z, t.transform.rotation.w = qz, qw
        return t

    def destroy_node(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = DashboardBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
