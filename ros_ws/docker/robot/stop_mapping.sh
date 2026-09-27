#!/bin/bash
# Stop mapping. Pass --save NAME first to keep the map (maps/NAME.posegraph/.data).
set -euo pipefail
cd "$(dirname "$0")/../../.."
if [[ "${1:-}" == "--save" && -n "${2:-}" ]]; then
  docker exec rescuebot-mapping bash -c "source /opt/ros/jazzy/setup.bash && \
    ros2 service call /slam_toolbox/serialize_map slam_toolbox/srv/SerializePoseGraph \"{filename: '/tmp/$2'}\""
  mkdir -p maps
  docker cp "rescuebot-mapping:/tmp/$2.posegraph" maps/ && docker cp "rescuebot-mapping:/tmp/$2.data" maps/
  echo "Saved maps/$2.posegraph and maps/$2.data"
fi
docker rm -f rescuebot-mapping >/dev/null && echo "Mapping stopped."
