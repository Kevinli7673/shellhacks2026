#!/bin/bash
# Build the robot ROS 2 image and start mapping in the background.
# Needs the dashboard running (with --sensors live) on this Pi. Read-only:
# the container polls the dashboard's HTTP API and never drives the motors.
#   ros_ws/docker/robot/run_mapping.sh            # build + start
#   ros_ws/docker/robot/run_mapping.sh --no-imu   # ignore the BNO055 heading
set -euo pipefail
cd "$(dirname "$0")/../../.."
use_imu=true
[[ "${1:-}" == "--no-imu" ]] && use_imu=false
docker build -f ros_ws/docker/robot/Dockerfile -t rescuebot-robot:jazzy .
docker rm -f rescuebot-mapping >/dev/null 2>&1 || true
docker run -d --name rescuebot-mapping --network host --ipc host \
  rescuebot-robot:jazzy ros2 launch rescuebot_robot mapping.launch.py use_imu:="$use_imu"
echo "Mapping started. Map page: http://$(hostname -I | awk '{print $1}'):8090/"
echo "Logs: docker logs -f rescuebot-mapping    Stop: ros_ws/docker/robot/stop_mapping.sh"
