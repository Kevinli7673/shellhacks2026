#!/bin/bash
# Build the robot ROS 2 image and start mapping + Nav2 autonomy in the background.
# Needs the dashboard on this Pi with:
#   --motor-backend bridge --sensors live --camera-backend live --allow-physical-autonomy
# The container gets only the dashboard's autonomy sockets (/tmp/rescuebot-UID/ros),
# never the bridge or serial port. The operator still enables driving and
# clicks Start autonomy; any key or Stop takes over.
#   ros_ws/docker/robot/run_autonomy.sh
set -euo pipefail
cd "$(dirname "$0")/../../.."
ros_dir="${RESCUEBOT_RUN_DIR:-/tmp/rescuebot-$(id -u)}/ros"
if [[ ! -d "$ros_dir" ]]; then
  echo "No $ros_dir: start the dashboard with --allow-physical-autonomy first." >&2
  exit 1
fi
docker build -f ros_ws/docker/robot/Dockerfile -t rescuebot-robot:jazzy .
docker rm -f rescuebot-mapping rescuebot-autonomy >/dev/null 2>&1 || true
docker run -d --name rescuebot-autonomy --network host --ipc host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e ROS_LOG_DIR=/tmp/ros-log \
  -e XDG_RUNTIME_DIR=/run/rescuebot -v "$ros_dir:/run/rescuebot" \
  rescuebot-robot:jazzy ros2 launch rescuebot_robot autonomy.launch.py
echo "Autonomy stack started. Map page: http://$(hostname -I | awk '{print $1}'):8090/"
echo "Logs: docker logs -f rescuebot-autonomy    Stop: docker rm -f rescuebot-autonomy"
