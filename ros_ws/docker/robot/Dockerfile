# Slim ROS 2 image for the physical robot (Raspberry Pi 5, arm64): SLAM, and
# Nav2 for autonomy. No Gazebo, RViz, or desktop. See ros_ws/docker/robot/README.md.
FROM ros:jazzy-ros-base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ros-jazzy-slam-toolbox \
    ros-jazzy-nav2-bringup \
    ros-jazzy-nav2-collision-monitor \
    ros-jazzy-nav2-rotation-shim-controller \
    python3-yaml \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ros_ws
# The autonomy adapter and mission manager share the dashboard's IPC codecs.
COPY app/rescuebot/ /opt/rescuebot-app/rescuebot/
COPY ros_ws/src/rescuebot_robot/ src/rescuebot_robot/
COPY ros_ws/src/rescuebot_sim_bridge/ src/rescuebot_sim_bridge/
COPY ros_ws/src/rescuebot_navigation/ src/rescuebot_navigation/
RUN source /opt/ros/jazzy/setup.bash \
    && colcon build --packages-select rescuebot_robot rescuebot_sim_bridge rescuebot_navigation \
       --event-handlers console_direct+

# Host networking (see run_mapping.sh) keeps DDS on this Pi only.
ENV ROS_DOMAIN_ID=42 \
    ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/rescuebot-app

COPY ros_ws/docker/robot/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
CMD ["ros2", "launch", "rescuebot_robot", "mapping.launch.py"]
