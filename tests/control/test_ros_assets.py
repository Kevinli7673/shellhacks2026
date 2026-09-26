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
        self.assertEqual(plugin.findtext("topic"), "cmd_vel")
        self.assertEqual(plugin.findtext("odom_topic"), "odom")

    def test_bridge_declares_required_simulated_topics(self) -> None:
        text = (ROS / "rescuebot_gazebo" / "config" / "bridge.yaml").read_text()
        for topic in ("/cmd_vel", "/scan", "/imu/data", "/odom"):
            self.assertIn(f'ros_topic_name: "{topic}"', text)

    def test_world_is_well_formed(self) -> None:
        world = ROS / "rescuebot_gazebo" / "worlds" / "indoor_maze.sdf"
        self.assertEqual(ET.parse(world).getroot().tag, "sdf")


if __name__ == "__main__":
    unittest.main()
