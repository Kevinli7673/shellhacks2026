# Rescuebot

Rescuebot is a mecanum-wheel search-and-rescue robot built for ShellHacks 2026.
It is meant to go where a person can't easily or safely go (a collapsed or
smoke-filled room, a cluttered space), look for people, and tell its operator
who it found, where they are, and whether they seem to need help.

An operator drives it from a web browser with the keyboard. On the robot, an AI
camera detects people and a spinning LiDAR sees the room. Gemini looks at each
person the camera finds and says whether they need help. The robot also speaks
its findings, sounds a buzzer, and flashes a light when it spots someone. It can
map the room with ROS 2 SLAM. With an explicit opt-in, it can also drive itself:
search the room, count the people it finds, and come back.

Safety comes first throughout. Driving has to be armed on purpose, Stop
overrides everything, and every link in the chain has its own watchdog. If the
Wi-Fi, the browser, the server, or the USB cable drops, the motors shut off and
stay off until someone arms them again.

## Contents

- [What it can do](#what-it-can-do)
- [Hardware](#hardware)
- [How it works](#how-it-works)
- [Safety design](#safety-design)
- [Running the robot](#running-the-robot)
- [Developing without the robot](#developing-without-the-robot)
- [Tests](#tests)
- [Repository layout](#repository-layout)
- [Status and limitations](#status-and-limitations)
- [Team](#team)
- [Project documents](#project-documents)

## What it can do

### Drive from a browser

- **W/S** drive forward and back, **A/D** strafe left and right, and
  **←/→** rotate counterclockwise and clockwise. Keys combine, so W+D moves
  diagonally and W+→ drives in an arc.
- **↑/↓** change speed in 10% steps (10–100%, starting at 30%). Speed keys
  alone never move the robot.
- **Space** or the **Stop** button stops at once and disarms.
- Driving starts only after **Enable driving**, and only once the robot
  controller confirms it is armed. The dashboard shows "Arming…" until then.
- Only one browser can drive at a time. Every other open dashboard is
  read-only but can still press Stop.

### See what the robot sees

- **Live camera:** annotated video from the Raspberry Pi AI Camera, with a box
  around every person it detects.
- **Person detection:** runs on the camera's own chip (Sony IMX500, YOLO11n),
  so it doesn't load the Pi's CPU. A box only appears after 3 detections in
  5 frames, and it disappears after 1 s without a detection, so stale boxes
  don't linger.
- **LiDAR view:** a top-down radar of the RPLIDAR C1's 360° scan. The robot is
  in the middle with its front pointing up, the camera's field of view is shown
  as a wedge, and the nearest obstacle is circled and labeled
  (for example "Nearest 0.90 m · 25° right").
- **Live map:** the SLAM map of the room, drawn around the robot as it moves.
  It shows recent person sightings and the people counted during a room search,
  has zoom (1.5–12 m across) and heading-up or north-up views, and a color key
  under it.
- **Robot state:** the signal chain from browser to firmware with a status lamp
  for each link, the four wheel speeds the firmware acknowledged, IMU heading,
  and link timing.

### Report on the people it finds

- **Gemini triage** (`--gemini`): when a person appears, the dashboard sends
  the camera frame to Gemini and gets back:
  - how many **unique** people are in view (ignoring duplicate boxes,
    reflections, posters, and screens),
  - whether they **need help**, and how urgently (none, low, medium, or high),
  - their posture, any hazards, a short summary, and a recommended action.

  The "Gemini triage" panel shows the assessed frame, the answer, and earlier
  results, and has an **Ask Gemini now** button. Every assessment is saved
  (image and JSON) under `~/rescuebot_runs/gemini/`. Gemini only looks at pictures; it never controls the robot.
- **Voice** (`--voice`): the robot speaks through its own speaker, using
  ElevenLabs text-to-speech with a local fallback. It says things like how
  many people it sees, where they are, and whether they need help. With Gemini
  on, Gemini writes what the robot says. Phrases are cached, so repeats play
  instantly and keep working offline.
- **Buzzer and light:**
  - **Manual:** the dashboard's Buzzer and Light buttons turn them on and off.
    The buttons only show "On" once the robot confirms it.
  - **Automatic** (`--auto-accessories`): when a person first appears, the
    buzzer sounds for 2 s and the light flashes 5 times. When the camera sees
    that the room is dark (below 10 lux for 5 s), the light turns on, and it
    turns off again once the room is bright (above 40 lux for 5 s).

### Map and search on its own (opt-in)

- **Mapping:** ROS 2 SLAM Toolbox builds a map of the room from the LiDAR
  while you drive manually. This can't move the robot.
- **Autonomy** (`--allow-physical-autonomy`, off by default): Nav2 plans
  paths on that map and can drive the real robot. From the dashboard you can:
  - **Send goal:** move a set distance forward or back, left or right
    (0.1–2 m).
  - **Search room & return:** cover the room, count each distinct person the
    camera sees (grouping repeat sightings on the map), pair each person with
    Gemini's assessment, then return to where the search started and list
    everyone it found.
- Any movement key, Stop, a firmware disarm, or a lost connection cancels
  autonomy at once. The operator is always in control.

## Hardware

| Part | Role |
|---|---|
| Raspberry Pi 5 | Runs the dashboard, motor bridge, camera service, LiDAR reader, voice, Gemini client, and the ROS 2 container |
| Raspberry Pi AI Camera (Sony IMX500) | Person detection on the sensor, plus annotated video |
| Slamtec RPLIDAR C1 | 360° scans for the LiDAR view, SLAM mapping, and obstacle avoidance |
| Adafruit QT Py ESP32-S2 | Robot controller: arming, watchdog, mecanum mixing, motors, IMU, buzzer, light |
| Hiwonder mecanum wheel chassis | Frame, four mecanum wheels, and four DC gear motors |
| Adafruit Motor/Stepper/Servo Shield for Arduino v2.3 (I2C 0x60) | Drives the four DC motors |
| 6 × AA battery pack | Motor power, through the motor shield |
| Energizer 10,000 mAh 22.5 W USB-C power bank | Powers the Raspberry Pi |
| Bosch BNO055 (I2C 0x28) | Heading, used for odometry during SLAM |
| MAX98357A I2S amplifier + 1 W speaker | The robot's voice |
| Buzzer on QT Py A3 (GPIO 8) | Alert tone (a 2 kHz square wave, so passive buzzers work too) |
| Adafruit NeoPixel Jewel 7 (RGBW) on QT Py RX (GPIO 16) | Alert flashes and the light for dark rooms |

**Motor wiring:** front-left = M2, front-right = M4, rear-left = M1 (inverted),
rear-right = M3 (inverted). The "front" is the camera end. Wiring differences
are corrected only in `firmware/include/chassis_config.h`, never in the mixing
math. The firmware caps motor output at 60/255 PWM (`hardware_pwm_ceiling`).
The dashboard's speed limit is its speed percentage of 180, so every dashboard
speed above about 33% (manual or `--autonomy-speed`) reaches the same 60 PWM
top speed.

## How it works

```
Browser (keyboard, buttons)
  │ WebSocket
  ▼
FastAPI dashboard on the Pi ──────────────── camera service (child process)
  │ control service: single owner, arming,      AI Camera → detections + MJPEG video
  │ 250 ms input deadline                     ─ LiDAR reader (child process)
  │ Unix datagrams (latest command only)        RPLIDAR C1 → 360 one-degree bins
  ▼                                           ─ voice, Gemini, buzzer/light automation
motor bridge (separate process)                 (own threads; never touch driving)
  │ 250 ms ACK deadline
  │ USB serial, newline-delimited JSON, 115200 baud
  ▼
ESP32-S2 firmware ── 500 ms watchdog, arm/disarm, session + sequence checks
  │ mecanum mixing → wheel config
  ▼
Motor Shield V2 (I2C) → four motors        BNO055 IMU, buzzer, NeoPixel light

ROS 2 Jazzy (Docker, optional):
  dashboard /api/lidar + IMU heading → /scan + odometry → SLAM Toolbox → map
  Nav2 → Collision Monitor → autonomy socket → dashboard (same safety checks)
```

- **Why a separate controller.** The ESP32-S2 is the only thing that talks to
  the motors. Its own 500 ms watchdog stops them even if the entire Pi freezes.
- **Why separate processes.** The camera, LiDAR reader, and motor bridge each
  run in their own process, so video encoding, inference, or a stuck sensor
  can never delay a Stop. The dashboard only ever holds the newest reading from
  each.
- **Why "latest command only."** Commands to the bridge expire and are never
  queued, so a network hiccup can't make the robot replay a backlog of old
  movements.
- **Mecanum mixing** runs on the ESP32. The forward/sideways input is
  normalized so diagonals aren't faster, then:

  ```
  front_left  = forward + sideways + turn
  front_right = forward − sideways − turn
  rear_left   = forward − sideways + turn
  rear_right  = forward + sideways − turn
  ```

  If any wheel would exceed the limit, all four are scaled together. The Python
  mock and the C++ firmware must produce identical results on shared test
  fixtures.
- **Serial protocol.** The protocol is newline-delimited JSON, each line at
  most 200 bytes.
  - Pi → ESP32: `drive`, `arm`/`disarm`, and `accessories`. Each carries a
    session and sequence number, and stale or repeated ones are ignored.
  - ESP32 → Pi: drive acknowledgments with the four wheel values,
    `arm_ack`/`disarm_ack`/`accessories_ack`, `fault` (boot, malformed or
    oversized packet, watchdog expired), `imu` at 20 Hz, and diagnostic
    `status` and `imu_diag` lines.
  - `fixtures/serial_protocol_vectors.json` holds shared test cases that the
    Python and firmware sides both replay.
- **ROS 2 without a native install.** The Pi runs Debian 13, which has no ROS 2
  packages, so ROS 2 Jazzy runs in a slim Docker image. The ROS side reads the
  LiDAR and IMU through the dashboard's HTTP API. It never opens a serial port
  or the motor bridge, and it can only drive through the same autonomy socket
  and safety checks as everything else.

## Safety design

| Layer | Stops the motors when |
|---|---|
| Browser | the window loses focus, the tab is hidden, the connection drops, or Space/Stop is pressed |
| Control service | no fresh browser input for 250 ms |
| Motor bridge | no fresh command, or no advancing acknowledgment, for 250 ms |
| ESP32 firmware | no valid drive command for 500 ms |
| Firmware loop watchdog | the controller's main loop stalls for 3 s (the chip resets and boots disarmed) |

- Boot, faults, and reconnects always leave the robot **disarmed**. Arming
  needs an explicit click, all movement keys released, healthy links, and a
  firmware acknowledgment.
- Autonomy is off unless the dashboard is started with
  `--allow-physical-autonomy`. Even then it needs Enable driving plus Start
  autonomy, and any key or Stop takes over.
- The buzzer and light switch off at boot, on a new serial session, and when
  the controlling browser disconnects (except the automatic dark-room light,
  which stays on while the room is dark). They never affect driving.
- Replay never drives motors, and simulation output can never reach the serial
  port.
- The acceptance checklist is in
  [docs/acceptance-runbook.md](docs/acceptance-runbook.md). It covers stop
  timing, fault shutdowns, USB unplugging, and a 15-minute endurance run.

## Running the robot

On the Raspberry Pi, from a checkout of this repository:

```bash
python3 -m venv --system-site-packages .venv   # system packages provide picamera2
.venv/bin/pip install -e '.[voice]'
tools/robot/start_robot.sh                      # also works as "restart"
tools/robot/stop_robot.sh
```

`start_robot.sh` starts, in order:
1. The motor bridge, on the QT Py's `/dev/serial/by-id/` path.
2. The dashboard, with the live camera, LiDAR, voice, Gemini, automatic
   buzzer/light, and physical autonomy enabled.
3. A fresh ROS 2 autonomy stack.

It stops anything it started before, waits for navigation to be ready, and then
prints a health summary. Open `http://<pi-address>:8000/` in a browser on the
same network. Logs and pid files go to `~/rescuebot-logs/`.

`start_robot.sh` settings (environment variables):
- `RESCUEBOT_PYTHON`: the Python to use (default `.venv/bin/python` in the
  checkout). It always runs this checkout's code (`PYTHONPATH=app`).
- `RESCUEBOT_SERIAL_DEVICE`: the QT Py port (default: the Adafruit QT Py
  ESP32-S2 entry under `/dev/serial/by-id/`).
- `RESCUEBOT_AUTONOMY_SPEED` and `RESCUEBOT_AUTONOMY_MIN_PWM`: both default
  to 60.
- `RESCUEBOT_LOG_DIR`: where logs go.

- **API keys:** `GEMINI_API_KEY` for Gemini triage and `ELEVENLABS_API_KEY` for
  ElevenLabs voice. Set them in the environment or as `export` lines in
  `~/.bashrc`. Without them, those features say so on the dashboard; voice
  falls back to espeak-ng.
- **Before a demo:** SLAM adds a scan every 0.4 s even when the robot is still,
  and slows down after an hour or two ("Waiting for Collision Monitor" on the
  dashboard). Run `start_robot.sh` again to start a fresh map.
- **First drive:** raise the chassis so the wheels are off the ground, then
  Enable driving and check each key.

To start the pieces by hand (for example, manual driving only):

```bash
.venv/bin/python -m rescuebot.motor_bridge --transport serial \
  --serial-device /dev/serial/by-id/usb-Adafruit_QT_Py_ESP32-S2_...-if00
.venv/bin/rescuebot-dashboard --motor-backend bridge --camera-backend live --sensors live
```

Dashboard options:
- `--voice`, `--gemini`, `--auto-accessories`
- `--allow-physical-autonomy`, `--autonomy-speed`, `--autonomy-min-pwm`

The motor bridge only accepts stable `/dev/serial/by-id/` paths. It refuses
bare `ttyUSB`/`ttyACM` names, so motor commands can never go to the LiDAR's
USB adapter by mistake.

### Firmware

The firmware is a PlatformIO project in `firmware/` (board
`adafruit_qtpy_esp32s2`). Stop the motor bridge first (it holds the port), then
flash it with:

```bash
tools/robot/stop_robot.sh
cd firmware
pio run -e esp32-s2 -t upload --upload-port /dev/serial/by-id/usb-Adafruit_QT_Py_ESP32-S2_...-if00
```

It boots disarmed. If the upload can't connect: hold BOOT, tap RESET, and
release BOOT, then retry.

## Developing without the robot

Everything has a mock, so the dashboard runs on any laptop:

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/rescuebot-dashboard --sensors mock      # mock motors, camera, and LiDAR room
```

Open `http://localhost:8000/`. Other modes:
- **Replay detections:** `--camera-backend replay --replay-path <file.jsonl>`
  replays a recorded detection file at its original pace. To record one on the
  Pi, run `python ai_camera_detect.py --json --headless --only person > run.jsonl`.
- **Simulation:** `ros_ws/` contains a full Gazebo Harmonic + Nav2 simulation
  of the robot, with a search-house world. It runs in Docker on macOS or Linux;
  see [ros_ws/README.md](ros_ws/README.md) and
  [ros_ws/docker/README.md](ros_ws/docker/README.md). The dashboard drives it
  with `--motor-backend gazebo`.

## Tests

The tests need two packages that the dashboard itself doesn't:

```bash
.venv/bin/pip install pytest httpx                  # httpx is used by FastAPI's test client
PYTHONPATH=app .venv/bin/python -m pytest tests     # Python: control, protocol, sensors, autonomy, web
cd firmware && pio test -e native                   # firmware logic on the host: mixing, protocol, watchdog, wiring
cd firmware && pio run -e esp32-s2                  # firmware build for the QT Py
```

`PYTHONPATH=app` makes the tests use this checkout's code even if the venv's
editable install points at another checkout.

- **Optional integration tests:** with `RESCUEBOT_INTEGRATION=1`, two more
  tests run. `tests/integration/test_browser.py` drives the real dashboard in
  headless Chrome with real key presses. `test_firmware_link.py` compiles the
  firmware logic and talks to it over a virtual serial port through the real
  bridge.
  They need Chrome or Chromium, and `clang++` or `g++`. Both use their own
  temporary sockets and a simulated serial port, never the robot.
- **ROS package tests** run inside the robot's ROS 2 image. After
  `ros_ws/docker/robot/run_autonomy.sh` has built `rescuebot-robot:jazzy`:

  ```bash
  docker run --rm -v "$PWD:/src:ro" -w /src/ros_ws/src/rescuebot_navigation \
    -e PYTHONPATH=/src/ros_ws/src/rescuebot_navigation:/src/app rescuebot-robot:jazzy \
    bash -c "source /opt/ros/jazzy/setup.bash && python3 -m pytest -q -p no:cacheprovider test"
  ```

Latest results on `main` (Raspberry Pi 5, Python 3.13, pytest 9.1, PlatformIO
6.2), 2026-09-27:

| Suite | Result |
|---|---|
| Python `tests/` | 271 passed, 2 skipped (the two integration tests) |
| Python with `RESCUEBOT_INTEGRATION=1` (`tests/integration`) | 2 passed |
| Firmware `pio test -e native` | 59/59 passed |
| Firmware `pio run -e esp32-s2` | builds (RAM 8.6%, flash 22.2%) |
| ROS `rescuebot_navigation` tests (at `7fa55a9`; `main` differs only in the README) | 58 passed |

## Repository layout

| Path | Contents |
|---|---|
| `app/rescuebot/` | Dashboard (`web.py`), control service, motor bridge and serial link, camera/LiDAR/voice/Gemini services, autonomy and search IPC |
| `app/rescuebot/static/` | Dashboard page: plain HTML, CSS, and JavaScript |
| `ai_camera_detect.py` | AI Camera detector: IMX500 inference, annotated MJPEG video, JSON detection output |
| `firmware/` | ESP32-S2 PlatformIO project and native tests |
| `fixtures/` | Shared serial-protocol and mecanum test vectors, detection recordings |
| `ros_ws/` | ROS 2 packages: robot description, Gazebo simulation, navigation and search, physical-robot mapping/autonomy, Docker images |
| `tools/robot/` | One-command start/stop for the robot |
| `tools/sensors/`, `tools/speaker/` | Standalone sensor recorder, LiDAR helpers, speaker setup and test |
| `tests/` | Python tests, plus the optional browser and firmware-link integration tests |
| `docs/` | Acceptance runbook, per-workstream handoffs, original plan |

## Status and limitations

- **Verified on the real robot:**
  - Manual driving in every direction, with the verified wheel mapping.
  - Arming, Stop, and the browser-timeout stop.
  - Person detection, LiDAR, voice, and the manual buzzer and light buttons.
  - Gemini triage with the live camera.
  - SLAM mapping, and the live map on the dashboard.
  - Forward and backward set-distance autonomy goals.
  - IMU heading: available, and no controller resets since the USB-write fix
    (`fadea2e`), over about 1.8 hours of mostly idle running.
- **Not yet verified on the real robot:**
  - A full room search, end to end (search, count people, return).
  - Left and right set-distance goals: the wheels turn, but the robot barely
    moves (see weak strafing).
  - The automatic dark-room light, and the automatic person alert since the
    light was rewired.
  - The acceptance runbook's USB-unplug, fault, and 15-minute endurance tests.
- **Controller resets:** the ESP32 used to reset itself (3 s loop watchdog)
  when the Pi paused reading USB. That showed as "Motor bridge unavailable",
  and the IMU stayed off until RESET. Since `fadea2e` the firmware drops a
  line instead of waiting. If it happens again, the bridge log shows the
  `imu_diag` line with `reset_reason` and `wdt_stage` (the loop step that hung).
- **No wheel encoders:** SLAM is the only position source. Drive slowly while
  mapping, and a jump in localization stops a search.
- **Weak strafing:** strafing moves poorly because of the robot's weight
  distribution. Room search avoids strafing for that reason.
- **Close-range blind spot:** obstacles closer than about 20 cm (8 in) to the
  LiDAR are ignored by navigation, so the robot's own parts don't block it.
- **Room search assumptions:** coverage assumes about 0.9 m of all-around
  sensing, but the camera only faces forward.
- **Autonomy is new:** it's opt-in and should be run with a spotter.
- **Pi power:** the 22.5 W power bank can't supply the 5 A a Raspberry Pi 5
  asks for, so the Pi limits its USB ports' total current. With the camera,
  LiDAR, controller, and speaker all running, it has reported undervoltage
  before. A 27 W (5 V, 5 A) supply avoids this.
- **Autonomy speed floor:** `--autonomy-min-pwm` is applied before the
  firmware's 60 PWM cap. At `--autonomy-speed` above about 33%, the firmware
  scales small commands down again, so slow approach commands can fall below
  the speed at which the wheels turn.

## Team

- Shade Rahman: dashboard, control service, motor bridge, serial protocol,
  integration and hardware bring-up, and physical ROS 2 mapping and autonomy
  (committed under the a1vcm account)
- Isabelle Mathew: ESP32-S2 firmware, buzzer and light
- a1vcm (GitHub): AI Camera detection, LiDAR and sensor tools, speaker and
  voice, Gemini triage, and help with the hardware
- Kevin Li: repository owner, and the robot's hardware (chassis, motors,
  wiring)

## Project documents

Contributors: read [AGENTS.md](AGENTS.md), the current-state section of
[changes.md](changes.md), and the relevant parts of
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) before starting work.

- [Implementation plan](IMPLEMENTATION_PLAN.md): approved design and safety rules
- [changes.md](changes.md): project history, decisions, and test evidence
- [AGENTS.md](AGENTS.md) and [WORKSTREAMS.md](WORKSTREAMS.md): repository and
  collaboration rules
- [PRODUCT.md](PRODUCT.md) and [DESIGN.md](DESIGN.md): dashboard product and
  design system
- [docs/acceptance-runbook.md](docs/acceptance-runbook.md): physical acceptance
  checklist
- [docs/handoffs/](docs/handoffs/): per-workstream details (Gemini, speaker,
  autonomy, firmware, dashboard, camera)
- [Original project plan](docs/reference/rescue_robot_plan.md)
