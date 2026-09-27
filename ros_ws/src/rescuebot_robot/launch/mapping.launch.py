"""Physical-robot mapping: dashboard LiDAR/IMU bridge + slam_toolbox + map web page.

Read-only with respect to the robot: nothing here can drive the motors. Drive
manually from the dashboard while the map builds.

    ros2 launch rescuebot_robot mapping.launch.py
    ros2 launch rescuebot_robot mapping.launch.py dashboard_url:=http://127.0.0.1:8000 use_imu:=false
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    share = Path(get_package_share_directory("rescuebot_robot"))
    slam_share = Path(get_package_share_directory("slam_toolbox"))
    dashboard_url = LaunchConfiguration("dashboard_url")
    use_imu = LaunchConfiguration("use_imu")
    map_port = LaunchConfiguration("map_port")

    return LaunchDescription([
        DeclareLaunchArgument("dashboard_url", default_value="http://127.0.0.1:8000"),
        DeclareLaunchArgument("use_imu", default_value="true",
                              description="Use the BNO055 heading as odometry yaw."),
        DeclareLaunchArgument("map_port", default_value="8090"),
        Node(
            package="rescuebot_robot",
            executable="dashboard_bridge",
            name="rescuebot_dashboard_bridge",
            output="screen",
            parameters=[{
                "dashboard_url": dashboard_url,
                "use_imu": ParameterValue(use_imu, value_type=bool),
                # Robot space: returns within 8 in of the LiDAR are the robot itself.
                "range_min": 0.203,
            }],
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(slam_share / "launch" / "online_async_launch.py")),
            launch_arguments={
                "slam_params_file": str(share / "config" / "slam_toolbox.yaml"),
                "use_sim_time": "false",
            }.items(),
        ),
        Node(
            package="rescuebot_robot",
            executable="map_viewer",
            name="rescuebot_map_viewer",
            output="screen",
            parameters=[{"port": ParameterValue(map_port, value_type=int)}],
        ),
    ])
