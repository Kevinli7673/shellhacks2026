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
  and has zoom (1.5–12 m across) and heading-up or north-up views.
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
  results, and has an **Ask Gemini now** button. Every assessment is saved to
  disk. Gemini only looks at pictures; it never controls the robot.
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
    that the room is dark (below 10 lux for 5 s), the light turns on.

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
| Adafruit Motor Shield V2 (I2C 0x60) | Drives the four DC motors |
| Four mecanum wheels and DC motors | Omnidirectional drive |
| Bosch BNO055 (I2C 0x28) | Heading, used for odometry during SLAM |
| MAX98357A I2S amplifier + 1 W speaker | The robot's voice |
| Buzzer on QT Py A3 (GPIO 8) | Alert tone (a 2 kHz square wave, so passive buzzers work too) |
| Adafruit NeoPixel Jewel 7 (RGBW) on QT Py RX (GPIO 16) | Alert flashes and the light for dark rooms |

_TODO (Pi): add the motor model and voltage, the battery or power supply for
the motors and for the Pi, and the chassis._

**Motor wiring:** front-left = M2, front-right = M4, rear-left = M1 (inverted),
rear-right = M3 (inverted). The "front" is the camera end. Wiring differences
are corrected only in `firmware/include/chassis_config.h`, never in the mixing
math. The motor output is capped at 60/255 PWM.

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

It then prints a health summary. Open `http://<pi-address>:8000/` in a browser
on the same network.

- **API keys:** `GEMINI_API_KEY` for Gemini triage and `ELEVENLABS_API_KEY` for
  ElevenLabs voice. Set them in the environment or as `export` lines in
  `~/.bashrc`. Without them, those features say so on the dashboard; voice
  falls back to espeak-ng.
- **Before a demo:** SLAM slows down after an hour or two of mapping, so run
  `start_robot.sh` again to start a fresh map.
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
`adafruit_qtpy_esp32s2`). Flash it with:

```bash
cd firmware
pio run -e esp32-s2 -t upload --upload-port /dev/ttyACM0
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

```bash
PYTHONPATH=app .venv/bin/python -m pytest tests     # Python: control, protocol, sensors, autonomy, web
cd firmware && pio test -e native                   # firmware logic on the host: mixing, protocol, watchdog, wiring
cd firmware && pio run -e esp32-s2                  # firmware build for the QT Py
```

- **Optional integration tests:** with `RESCUEBOT_INTEGRATION=1`, two more
  tests run. `tests/integration/test_browser.py` drives the real dashboard in
  headless Chrome with real key presses. `test_firmware_link.py` compiles the
  firmware logic and talks to it over a virtual serial port through the real
  bridge.
- **ROS package tests** run inside the ROS 2 image (see the `ros_ws` READMEs).

_TODO (Pi): confirm the exact test commands, any extra test dependencies, and
the latest pass counts on `main`._

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
  - Arming and watchdogs.
  - Person detection, LiDAR, voice, the buzzer and light.
  - Gemini triage with the live camera.
  - SLAM mapping.
  - Forward and backward set-distance autonomy goals.
- **No wheel encoders:** SLAM is the only position source. Drive slowly while
  mapping, and a jump in localization stops a search.
- **Weak strafing:** strafing moves poorly because of the robot's weight
  distribution. Room search avoids strafing for that reason.
- **Close-range blind spot:** obstacles closer than about 20 cm (8 in) to the
  LiDAR are ignored by navigation, so the robot's own parts don't block it.
- **Room search assumptions:** coverage assumes about 0.9 m of all-around
  sensing, but the camera only faces forward.
- **Autonomy is new:** it's opt-in and should be run with a spotter.

_TODO (Pi): update this list with the latest physical results. For example: has
a full room search run end to end on the robot? Does the IMU now stay
available? Have any watchdog resets happened since `fadea2e`?_

## Team

- Shade Rahman: dashboard, control service, motor bridge, serial protocol,
  integration and hardware bring-up
- Isabelle Mathew: ESP32-S2 firmware, buzzer and light
- _TODO (Pi): name for GitHub user a1vcm_ (a1vcm): AI Camera
  detection, LiDAR and sensor tools, speaker and voice, Gemini triage, physical
  ROS 2 mapping and autonomy
- Kevin Li: repository owner

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
