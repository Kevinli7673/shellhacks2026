#!/usr/bin/env bash
set -e -o pipefail

source /opt/ros/jazzy/setup.bash
source /workspace/ros_ws/install/setup.bash
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
exec "$@"
