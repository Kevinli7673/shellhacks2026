"""Start mapping, Nav2, Collision Monitor, and mission-owned RViz goals."""

from launch import LaunchDescription
from launch.actions import GroupAction, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node, SetRemap
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import PathJoinSubstitution


def generate_launch_description():
    share = FindPackageShare("rescuebot_navigation")
    nav2_launch = PathJoinSubstitution([FindPackageShare("nav2_bringup"), "launch", "navigation_launch.py"])
    return LaunchDescription([
        Node(
            package="slam_toolbox",
            executable="async_slam_toolbox_node",
            name="slam_toolbox",
            parameters=[PathJoinSubstitution([share, "config", "slam_toolbox.yaml"])],
            output="screen",
        ),
        GroupAction([
            # Recovery behaviors and controllers must not publish directly to
            # Gazebo. Collision Monitor is the sole final ROS velocity filter.
            SetRemap(src="/cmd_vel", dst="/cmd_vel_nav"),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(nav2_launch),
                launch_arguments={
                    "use_sim_time": "true",
                    "autostart": "true",
                    "params_file": PathJoinSubstitution([share, "config", "nav2.yaml"]),
                }.items(),
            ),
        ]),
        Node(
            package="nav2_collision_monitor",
            executable="collision_monitor",
            parameters=[PathJoinSubstitution([share, "config", "collision_monitor.yaml"])],
            output="screen",
        ),
        Node(package="rescuebot_navigation", executable="rescuebot_mission_manager", output="screen"),
    ])
