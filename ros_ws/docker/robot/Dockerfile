# Slim ROS 2 image for the physical robot (Raspberry Pi 5, arm64): SLAM only.
# No Gazebo, RViz, or desktop. See ros_ws/docker/robot/README.md.
FROM ros:jazzy-ros-base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ros-jazzy-slam-toolbox \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ros_ws
COPY ros_ws/src/rescuebot_robot/ src/rescuebot_robot/
RUN source /opt/ros/jazzy/setup.bash \
    && colcon build --packages-select rescuebot_robot --event-handlers console_direct+

# Host networking (see run_mapping.sh) keeps DDS on this Pi only.
ENV ROS_DOMAIN_ID=42 \
    ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
    PYTHONUNBUFFERED=1

COPY ros_ws/docker/robot/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
CMD ["ros2", "launch", "rescuebot_robot", "mapping.launch.py"]
