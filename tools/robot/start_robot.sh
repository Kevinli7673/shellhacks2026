#!/bin/bash
# Start the whole robot for a demo, from this checkout:
#   motor bridge (QT Py over USB) -> dashboard (live camera, LiDAR, voice,
#   Gemini, auto buzzer/light, physical autonomy) -> fresh ROS 2 autonomy stack.
# Stops anything this script started before, so it is also the "restart" button.
# A fresh autonomy stack starts a new map: slam_toolbox slows down after an
# hour or two of mapping, so run this again before each demo search.
#
#   tools/robot/start_robot.sh          # then open http://<pi>:8000/
#   tools/robot/stop_robot.sh
#
# Settings (environment variables):
#   RESCUEBOT_PYTHON         Python with the dashboard's packages (default: .venv/bin/python)
#   RESCUEBOT_LOG_DIR        logs and pid files (default: ~/rescuebot-logs)
#   RESCUEBOT_SERIAL_DEVICE  QT Py serial port (default: the Adafruit QT Py by-id link)
#   RESCUEBOT_AUTONOMY_SPEED / RESCUEBOT_AUTONOMY_MIN_PWM  (default 60 / 60, tested 2026-09-27)
# API keys come from the environment or the export lines in ~/.bashrc:
# GEMINI_API_KEY (Gemini triage), ELEVENLABS_API_KEY (voice; espeak-ng without it).
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
logs="${RESCUEBOT_LOG_DIR:-$HOME/rescuebot-logs}"
python="${RESCUEBOT_PYTHON:-$repo/.venv/bin/python}"
speed="${RESCUEBOT_AUTONOMY_SPEED:-60}"
min_pwm="${RESCUEBOT_AUTONOMY_MIN_PWM:-60}"
mkdir -p "$logs"

if [[ ! -x "$python" ]]; then
  echo "No Python at $python. Set RESCUEBOT_PYTHON to the venv with the dashboard's packages." >&2
  exit 1
fi
device="${RESCUEBOT_SERIAL_DEVICE:-$(ls /dev/serial/by-id/usb-Adafruit_QT_Py_ESP32-S2_*-if00 2>/dev/null | head -1)}"
if [[ -z "$device" || ! -e "$device" ]]; then
  echo "QT Py not found on USB. Plug it in or set RESCUEBOT_SERIAL_DEVICE." >&2
  exit 1
fi
# ~/.bashrc returns early for non-interactive shells, so read only the key lines.
if [[ -f "$HOME/.bashrc" ]]; then
  eval "$(grep -E '^export (GEMINI_API_KEY|ELEVENLABS_API_KEY)=' "$HOME/.bashrc" || true)"
fi
[[ -n "${GEMINI_API_KEY:-}" ]] || echo "Warning: GEMINI_API_KEY not set; the Gemini panel will say so." >&2

"$repo/tools/robot/stop_robot.sh" --quiet

# Always run this checkout's code, whatever the venv's editable install points at.
export PYTHONPATH="$repo/app${PYTHONPATH:+:$PYTHONPATH}"
cd "$repo"

for name in motor_bridge dashboard; do
  if [[ -f "$logs/$name.log" ]]; then mv "$logs/$name.log" "$logs/$name.prev.log"; fi
done

nohup "$python" -u -m rescuebot.motor_bridge --transport serial --serial-device "$device" \
  > "$logs/motor_bridge.log" 2>&1 &
echo $! > "$logs/motor_bridge.pid"
sleep 2

nohup "$python" -u -c "import sys; sys.argv=['rescuebot-dashboard',
  '--motor-backend','bridge','--camera-backend','live','--sensors','live',
  '--voice','--auto-accessories','--gemini','--allow-physical-autonomy',
  '--autonomy-speed','$speed','--autonomy-min-pwm','$min_pwm']
from rescuebot.web import main; main()" > "$logs/dashboard.log" 2>&1 &
echo $! > "$logs/dashboard.pid"

# The autonomy container needs the dashboard's sockets; wait for the dashboard.
for _ in $(seq 1 60); do
  curl -sf -o /dev/null http://127.0.0.1:8000/api/state && break
  sleep 1
done
if ! curl -sf -o /dev/null http://127.0.0.1:8000/api/state; then
  echo "Dashboard did not start; see $logs/dashboard.log" >&2
  exit 1
fi

ros_ws/docker/robot/run_autonomy.sh > "$logs/autonomy-build.log" 2>&1 \
  || { echo "Autonomy stack failed to start; see $logs/autonomy-build.log" >&2; exit 1; }

echo "Waiting for navigation to be ready..."
for _ in $(seq 1 90); do
  ready="$(curl -s http://127.0.0.1:8000/api/state | "$python" -c \
    'import json,sys; print(json.load(sys.stdin)["autonomy"]["navigation"]["ready"])' 2>/dev/null || true)"
  [[ "$ready" == "True" ]] && break
  sleep 1
done

"$python" - <<'EOF'
import json, urllib.request
d = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/state"))
m, a = d["motor"], d["autonomy"]
nav = a.get("navigation", {})
print(f"Motor bridge: {'connected' if m['healthy'] else 'NOT connected'}"
      f" · IMU: {'available' if (m.get('imu') or {}).get('available') else 'unavailable'}")
print(f"Camera: {d['camera']['status']} · LiDAR: {d['sensors'].get('lidar', {}).get('status', 'off')}"
      f" · Gemini: {d['gemini'].get('state', 'off')}")
print(f"Autonomy: {'ready' if nav.get('ready') else nav.get('reason', 'unavailable')}"
      f" · speed {a.get('speed_percent')}%")
EOF
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):8000/   Map: port 8090   Logs: $logs"
