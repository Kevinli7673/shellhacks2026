"""Launch Gazebo Harmonic and the simulation-only Rescuebot command path."""

from launch import LaunchDescription
from launch.actions import ExecuteProcess, SetEnvironmentVariable
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import Command, FindExecutable, PathJoinSubstitution


def generate_launch_description():
    gazebo_share = FindPackageShare("rescuebot_gazebo")
    world = PathJoinSubstitution([gazebo_share, "worlds", "indoor_maze.sdf"])
    model = PathJoinSubstitution([gazebo_share, "models", "rescuebot", "model.sdf"])
    bridge_config = PathJoinSubstitution([gazebo_share, "config", "bridge.yaml"])
    model_path = PathJoinSubstitution([gazebo_share, "models"])
    robot_description = Command([
        FindExecutable(name="xacro"), " ",
        PathJoinSubstitution([FindPackageShare("rescuebot_description"), "urdf", "rescuebot.urdf.xacro"]),
    ])

    return LaunchDescription([
        SetEnvironmentVariable("GZ_SIM_RESOURCE_PATH", model_path),
        ExecuteProcess(cmd=["gz", "sim", "-r", world], output="screen"),
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{"robot_description": robot_description, "use_sim_time": True}],
        ),
        Node(
            package="ros_gz_sim",
            executable="create",
            arguments=["-file", model, "-name", "rescuebot", "-x", "0", "-y", "0", "-z", "0.02"],
            output="screen",
        ),
        Node(
            package="ros_gz_bridge",
            executable="parameter_bridge",
            parameters=[{"config_file": bridge_config}],
            output="screen",
        ),
        Node(package="rescuebot_sim_bridge", executable="rescuebot_sim_command_bridge", output="screen"),
        Node(package="rescuebot_sim_bridge", executable="rescuebot_sim_autonomy_adapter", output="screen"),
        Node(package="rescuebot_sim_bridge", executable="rescuebot_sim_odom_tf", output="screen"),
    ])
