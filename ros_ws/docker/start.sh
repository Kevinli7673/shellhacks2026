#!/usr/bin/env bash
set -e -o pipefail

pids=()
cleanup() {
    trap - EXIT INT TERM
    if ((${#pids[@]})); then
        kill -INT "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 0' INT TERM

Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp &
pids+=("$!")
for attempt in {1..100}; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done
xdpyinfo -display "$DISPLAY" >/dev/null

fluxbox &
pids+=("$!")
x11vnc -display "$DISPLAY" -localhost -rfbport 5900 -forever -shared -nopw &
pids+=("$!")
websockify --web=/usr/share/novnc 6080 localhost:5900 &
pids+=("$!")

world="${RESCUEBOT_WORLD:-indoor_maze.sdf}"
search_enabled=false
if [[ "$world" == "search_house.sdf" ]]; then search_enabled=true; fi
ros2 launch rescuebot_gazebo sim.launch.py world:="$world" &
pids+=("$!")
rescuebot-dashboard --motor-backend gazebo --camera-backend replay \
    --replay-path /workspace/fixtures/detections/person_appears_disappears.jsonl &
pids+=("$!")
ros2 launch rescuebot_navigation navigation.launch.py search_enabled:="$search_enabled" &
pids+=("$!")

# A failed service ends the container. Docker init reaps child processes;
# Stop/expiry handling remains in the existing host arbiter and ROS bridge.
wait -n "${pids[@]}"
