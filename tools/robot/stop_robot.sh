#!/bin/bash
# Stop what start_robot.sh started: autonomy container, dashboard (and its
# camera/LiDAR children), motor bridge. Stopping the bridge disarms the robot:
# the firmware watchdog stops the wheels within 500 ms of the last command.
#   tools/robot/stop_robot.sh [--quiet]
set -uo pipefail
logs="${RESCUEBOT_LOG_DIR:-$HOME/rescuebot-logs}"
quiet="${1:-}"
say() { [[ "$quiet" == "--quiet" ]] || echo "$@"; }

docker rm -f rescuebot-autonomy rescuebot-mapping >/dev/null 2>&1 && say "Stopped autonomy stack."
# Dashboard first, so it disarms through the bridge while the bridge still runs.
for name in dashboard motor_bridge; do
  pid_file="$logs/$name.pid"
  [[ -f "$pid_file" ]] || continue
  pid="$(cat "$pid_file")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
    say "Stopped $name ($pid)."
  fi
  rm -f "$pid_file"
done
