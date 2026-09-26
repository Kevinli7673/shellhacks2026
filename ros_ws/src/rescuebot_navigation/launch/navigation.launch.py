"""Launch the simulation mapping and goal-navigation lifecycle nodes."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution


def generate_launch_description():
    share = FindPackageShare("rescuebot_navigation")
    params = PathJoinSubstitution([share, "config", "nav2.yaml"])
    collision = PathJoinSubstitution([share, "config", "collision_monitor.yaml"])
    # Explicit nodes avoid unrelated docking/route servers and duplicate safety
    # filters as upstream navigation_launch.py evolves between Jazzy releases.
    nodes = [
        ("nav2_controller", "controller_server", [("cmd_vel", "/cmd_vel_nav")]),
        ("nav2_planner", "planner_server", []),
        ("nav2_behaviors", "behavior_server", [("cmd_vel", "/cmd_vel_nav")]),
        # RViz goals must go through our mission owner, not Nav2's direct
        # goal-topic subscriber, which otherwise submits a second action.
        ("nav2_bt_navigator", "bt_navigator", [("goal_pose", "/rescuebot/nav2_goal_pose_unused")]),
        ("nav2_velocity_smoother", "velocity_smoother", [("cmd_vel", "/cmd_vel_nav")]),
        ("nav2_collision_monitor", "collision_monitor", []),
    ]
    return LaunchDescription([
        DeclareLaunchArgument("search_enabled", default_value="false", choices=["true", "false"]),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(PathJoinSubstitution([
                FindPackageShare("slam_toolbox"), "launch", "online_async_launch.py",
            ])),
            launch_arguments={
                "use_sim_time": "true", "autostart": "true",
                "slam_params_file": PathJoinSubstitution([share, "config", "slam_toolbox.yaml"]),
            }.items(),
        ),
        *[Node(
            package=package, executable=name, name=name, output="screen",
            parameters=[params, collision], remappings=remappings,
        ) for package, name, remappings in nodes],
        Node(
            package="nav2_lifecycle_manager", executable="lifecycle_manager",
            name="lifecycle_manager_navigation", output="screen",
            parameters=[{"use_sim_time": True, "autostart": True,
                         "node_names": [name for _, name, _ in nodes]}],
        ),
        Node(package="rescuebot_navigation", executable="rescuebot_mission_manager", output="screen",
             parameters=[{"use_sim_time": True, "search_enabled": LaunchConfiguration("search_enabled")}]),
    ])
