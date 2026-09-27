"""Physical-robot autonomy: mapping + Nav2 + Collision Monitor + search, driving via the dashboard.

Everything in mapping.launch.py, plus:

- rescuebot_navigation (Nav2, Collision Monitor, mission manager) with real
  time and this robot's slam_toolbox; a search ends when the AI Camera sees a
  person (/rescuebot/people).
- The autonomy adapter, which forwards only Collision Monitor output
  (/cmd_vel_safe) to the dashboard's autonomy socket. The dashboard must run
  with --motor-backend bridge --allow-physical-autonomy; it still owns arming,
  mixing and the bridge, and manual keys or Stop take over at once.

    ros2 launch rescuebot_robot autonomy.launch.py
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    robot_share = Path(get_package_share_directory("rescuebot_robot"))
    navigation_share = Path(get_package_share_directory("rescuebot_navigation"))
    return LaunchDescription([
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(robot_share / "launch" / "mapping.launch.py"))),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(navigation_share / "launch" / "navigation.launch.py")),
            launch_arguments={
                "use_sim_time": "false",
                "slam": "false",
                "search_enabled": "true",
                "synthetic_target_enabled": "false",
                "person_topic": "/rescuebot/people",
            }.items(),
        ),
        Node(
            package="rescuebot_sim_bridge",
            executable="rescuebot_sim_autonomy_adapter",
            name="rescuebot_autonomy_adapter",
            output="screen",
            # Nav2's top speeds (nav2.yaml) map to the dashboard's full
            # autonomy speed (--autonomy-speed percent of the PWM ceiling).
            arguments=["--max-vx", "0.08", "--max-vy", "0.08", "--max-wz", "0.24"],
        ),
    ])
