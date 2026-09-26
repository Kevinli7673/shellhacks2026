"""Static checks for ROS/Gazebo files when ROS is unavailable on the host."""

from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).parents[2]
ROS = ROOT / "ros_ws" / "src"


class RosAssetTests(unittest.TestCase):
    def test_xacro_declares_the_base_and_all_required_sensor_frames(self) -> None:
        text = (ROS / "rescuebot_description" / "urdf" / "rescuebot.urdf.xacro").read_text()
        for name in ("base_link", "name=\"lidar\"", "name=\"imu\"", "name=\"ai_camera\"", "name=\"logitech_camera\""):
            self.assertIn(name, text)
        self.assertIn('xacro:wheel name="front_left"', text)
        self.assertIn('xacro:wheel name="rear_right"', text)

    def test_sdf_is_well_formed_and_uses_the_mecanum_drive_plugin(self) -> None:
        model = ROS / "rescuebot_gazebo" / "models" / "rescuebot" / "model.sdf"
        root = ET.parse(model).getroot()
        plugin = root.find(".//plugin[@name='gz::sim::systems::MecanumDrive']")
        self.assertIsNotNone(plugin)
        assert plugin is not None
        self.assertEqual(plugin.findtext("topic"), "/model/rescuebot/cmd_vel")
        self.assertEqual(plugin.findtext("odom_topic"), "/model/rescuebot/odometry")

    def test_bridge_endpoints_match_model_publishers_and_subscriber(self) -> None:
        model = ET.parse(ROS / "rescuebot_gazebo" / "models" / "rescuebot" / "model.sdf").getroot()
        bridge = (ROS / "rescuebot_gazebo" / "config" / "bridge.yaml").read_text()
        plugin = model.find(".//plugin[@name='gz::sim::systems::MecanumDrive']")
        for topic in (plugin.findtext("topic"), plugin.findtext("odom_topic")):
            self.assertIn(f'gz_topic_name: "{topic}"', bridge)
        for sensor in model.findall(".//sensor"):
            self.assertIn(f'gz_topic_name: "{sensor.findtext("topic")}"', bridge)
            self.assertTrue(sensor.findtext("gz_frame_id"))
        world = ET.parse(ROS / "rescuebot_gazebo" / "worlds" / "indoor_maze.sdf").getroot()
        self.assertIsNotNone(world.find(".//plugin[@name='gz::sim::systems::Imu']"))

    def test_bridge_declares_required_simulated_topics(self) -> None:
        text = (ROS / "rescuebot_gazebo" / "config" / "bridge.yaml").read_text()
        for topic in ("/cmd_vel", "/scan", "/imu/data", "/odom"):
            self.assertIn(f'ros_topic_name: "{topic}"', text)

    def test_world_is_well_formed(self) -> None:
        world = ROS / "rescuebot_gazebo" / "worlds" / "indoor_maze.sdf"
        self.assertEqual(ET.parse(world).getroot().tag, "sdf")

    def test_navigation_routes_nav2_through_collision_monitor_and_keeps_holonomic_velocity(self) -> None:
        navigation = ROS / "rescuebot_navigation"
        collision = (navigation / "config" / "collision_monitor.yaml").read_text()
        nav2 = (navigation / "config" / "nav2.yaml").read_text()
        self.assertIn("cmd_vel_in_topic: /cmd_vel_nav", collision)
        self.assertIn("cmd_vel_out_topic: /cmd_vel_safe", collision)
        self.assertIn("cmd_vel_topic: /cmd_vel_nav", nav2)
        self.assertIn("min_vel_y: -0.30", nav2)
        self.assertIn("max_vel_y: 0.40", nav2)


if __name__ == "__main__":
    unittest.main()
