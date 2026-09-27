"""Launch mapping and goal-navigation lifecycle nodes (simulation by default).

The physical robot (rescuebot_robot autonomy.launch.py) passes use_sim_time:=false,
slam:=false (it runs its own slam_toolbox), person_topic:=/rescuebot/people and
robot_params (its footprint and collision overrides).
"""

from copy import deepcopy
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
import yaml

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution


def generate_launch_description():
    share = FindPackageShare("rescuebot_navigation")
    params = PathJoinSubstitution([share, "config", "nav2.yaml"])
    collision = PathJoinSubstitution([share, "config", "collision_monitor.yaml"])
    # Loaded last; the physical robot passes rescuebot_robot's nav2_robot.yaml.
    robot_params = LaunchConfiguration("robot_params")
    # Derive the search controller from the accepted controller configuration:
    # all speed, acceleration, obstacle, and path-tracking settings stay shared.
    package_share = Path(get_package_share_directory("rescuebot_navigation"))
    config = yaml.safe_load((package_share / "config" / "nav2.yaml").read_text())
    search_controller = deepcopy(config["controller_server"]["ros__parameters"]["FollowPath"])
    search_controller["rotate_to_goal_heading"] = False
    search_controller["critics"].remove("RotateToGoal")
    search_parameters = {
        "controller_plugins": ["FollowPath", "SearchPath"],
        "goal_checker_plugins": ["goal_checker", "search_goal_checker"],
        "SearchPath": search_controller,
        "search_goal_checker": {
            "plugin": "nav2_controller::PositionGoalChecker",
            "xy_goal_tolerance": config["controller_server"]["ros__parameters"]["goal_checker"]["xy_goal_tolerance"],
            "stateful": True,
        },
    }
    use_sim_time = ParameterValue(LaunchConfiguration("use_sim_time"), value_type=bool)
    normal_tree = str(package_share / "behavior_trees" / "navigate_to_pose.xml")
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
        DeclareLaunchArgument("use_sim_time", default_value="true", choices=["true", "false"]),
        DeclareLaunchArgument("slam", default_value="true", choices=["true", "false"]),
        DeclareLaunchArgument("robot_params", default_value=params,
                              description="Parameter overrides loaded after nav2.yaml and collision_monitor.yaml."),
        DeclareLaunchArgument("goal_keeps_heading", default_value="false",
                              description="Set-distance goals keep the starting heading (mecanum robot) "
                                          "instead of finishing faced toward the destination."),
        DeclareLaunchArgument("person_topic", default_value="",
                              description="PoseArray of camera person sightings that ends a search."),
        DeclareLaunchArgument("synthetic_target_x", default_value="1.8"),
        DeclareLaunchArgument("synthetic_target_y", default_value="0.6"),
        DeclareLaunchArgument("synthetic_target_enabled", default_value="true", choices=["true", "false"]),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(PathJoinSubstitution([
                FindPackageShare("slam_toolbox"), "launch", "online_async_launch.py",
            ])),
            condition=IfCondition(LaunchConfiguration("slam")),
            launch_arguments={
                "use_sim_time": "true", "autostart": "true",
                "slam_params_file": PathJoinSubstitution([share, "config", "slam_toolbox.yaml"]),
            }.items(),
        ),
        *[Node(
            package=package, executable=name, name=name, output="screen",
            # robot_params loads after the derived search controller, so the
            # physical robot can override SearchPath as well as FollowPath.
            parameters=[params, collision] + (
                [search_parameters] if name == "controller_server" else
                [{"default_nav_to_pose_bt_xml": normal_tree}] if name == "bt_navigator" else []
            ) + [robot_params, {"use_sim_time": use_sim_time}], remappings=remappings,
        ) for package, name, remappings in nodes],
        Node(
            package="nav2_lifecycle_manager", executable="lifecycle_manager",
            name="lifecycle_manager_navigation", output="screen",
            parameters=[{"use_sim_time": use_sim_time, "autostart": True,
                         "node_names": [name for _, name, _ in nodes]}],
        ),
        Node(package="rescuebot_navigation", executable="rescuebot_mission_manager", output="screen",
             parameters=[{
                 "use_sim_time": use_sim_time,
                 "person_topic": LaunchConfiguration("person_topic"),
                 "goal_keeps_heading": ParameterValue(LaunchConfiguration("goal_keeps_heading"), value_type=bool),
                 "search_enabled": LaunchConfiguration("search_enabled"),
                 "synthetic_target_x": ParameterValue(LaunchConfiguration("synthetic_target_x"), value_type=float),
                 "synthetic_target_y": ParameterValue(LaunchConfiguration("synthetic_target_y"), value_type=float),
                 "synthetic_target_enabled": ParameterValue(LaunchConfiguration("synthetic_target_enabled"), value_type=bool),
             }]),
    ])
