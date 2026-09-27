# Project status, changes, and handoffs

This file records current progress and evidence.
IMPLEMENTATION_PLAN.md defines the approved design.
AGENTS.md defines repository rules.

Keep the current-state section concise.
Append dated entries to History; preserve previous entries.
Correct historical mistakes with a new entry rather than silently erasing them.

## Current state

- Milestone: manual driving + person detection + dashboard.
- Design: approved for implementation.
- Implementation: parallel workstreams in progress. Nothing has been
  integrated into main except the documentation, the cross-workstream request
  channel (b37213e), and ai_camera_detect.py (994802e).
- Stages A-D and camera stages: see docs/handoffs/dashboard-control.md on
  feature/dashboard-control.
- Stage E (firmware side) and F: firmware implemented on
  feature/esp32-controller. Native tests pass 52/52 and `pio run -e esp32-s2`
  builds. Not flashed; no hardware test.
- Stage L: deferred until physical milestone acceptance.
- Physical acceptance: not performed.
- Known application checkpoint: Stage 0 documentation baseline (tag:
  stage-0-docs, not yet pushed to origin). ESP32 firmware native checkpoint:
  f6213f3 (not integrated).
- Initial repository baseline: 34fb0b2, "Initial commit".
- Next action: integrator reviews and merges the workstream branches per
  WORKSTREAMS.md merge gates; resolve open hardware facts before Stage G.

## Active work

| Workstream | Branch/worktree | Affected areas/interfaces | Status | Updated |
|---|---|---|---|---|
| Documentation baseline | main | Plan, repository rules, handoff log, original reference, README | Complete | 2026-09-26 EDT |
| Dashboard and control | feature/dashboard-control | app/, static assets, control tests, mock backend | Active; its handoff (585d499) reports "ready for integration after review" | 2026-09-26 EDT |
| ESP32 controller | feature/esp32-controller | firmware/, firmware/test/, shield and IMU adapters | Active; native tests 52/52; esp32-s2 build succeeds; not integrated | 2026-09-26 EDT |

See WORKSTREAMS.md for the assigned development split and merge gates.
This table alone does not lock files or synchronize separate branches.

## Checkpoints

| Reference | Coverage | Validation evidence | Limitations |
|---|---|---|---|
| 34fb0b2 | Initial repository | Repository inspection only | No application or hardware validation |
| stage-0-docs | Documentation baseline | Original plan copied byte-for-byte; repository documentation inspected | No application or hardware validation. The tag has not been pushed to origin (checked 2026-09-26). |
| f6213f3 (feature/esp32-controller) | ESP32 firmware pure logic: mixing, session/watchdog, protocol codec, Controller, shared serial vectors | `pio test -e native` from firmware/: 52/52 pass (Windows 11, PlatformIO 6.2.0, GCC 15.2.0, ArduinoJson 6.21.6) | Native host only. Not integrated into main. No flashing or hardware test. |
| 1988d5a (feature/esp32-controller) | Same firmware; all Arduino-side sources | `pio run -e esp32-s2` from firmware/: SUCCESS, 0 warnings, RAM 4.8%, flash 20.6% (placeholder board esp32-s2-saola-1) | Build only. Board unconfirmed; not flashed; no hardware test. |

## Open hardware facts

- Exact ESP32-S2 board and I2C pins.
- Confirmed motor-shield revision/address.
- Wheel-channel mapping and direction inversions.
- Validated motor-output ceiling and power configuration.
- BNO055 communication and mounting/calibration details.
- Stable serial-device identity.
- Installed camera software and selected IMX500 model.

These do not block mock development.
Required hardware configuration must be resolved before real driving.

## History

### 2026-09-26 EDT - Documentation baseline

Status: completed.

Initial documentation commit: 46b08e7, "docs: add implementation and handoff guidance".

Decisions:

- Native Raspberry Pi OS/Python first; ROS 2 optional and deferred.
- FastAPI dashboard with an independent motor bridge and camera service.
- ESP32-S2 owns mixing, motor-shield I2C, IMU reads, and watchdog.
- Serial carries movement inputs, session identity, and sequence numbers.
- Exact mecanum equations retained.
- Positive axes: forward, right strafe, clockwise rotation.
- Hardware corrections use wheel mapping and direction inversions.
- ESP32 watchdog defaults to configurable 500 ms.
- Camera events are model-independent.
- JSONL recording/replay and mock backends are required.
- No scope expansion before physical milestone acceptance.
- Concurrent workstreams use separate branches/worktrees and explicit boundaries.

Validation:

- Repository and original project plan inspected.
- No code tests, firmware tests, or physical tests performed.

Next:

- Start the dashboard/control and ESP32-controller workstreams in parallel.
- Integrate only at the declared merge gates in WORKSTREAMS.md.

### 2026-09-26 EDT - Parallel workstream split

Status: ready to begin.

Decisions:

- Dashboard/control and ESP32 firmware work in separate branches and worktrees.
- docs/handoffs/dashboard-control.md and
  docs/handoffs/esp32-controller.md are the only handoff files their
  respective workstreams edit while work is concurrent.
- The serial contract and mecanum equations in IMPLEMENTATION_PLAN.md are
  frozen while both workstreams are active.
- The integrator alone updates this shared current-state summary after a merge.

Validation:

- Documentation boundaries reviewed against the approved milestone.
- No application, firmware, or physical tests performed.

### 2026-09-26 EDT - ESP32 controller: Stage E/F firmware, native tests, board build

- Workstream: ESP32 controller.
- Stage: E (firmware side) and F.
- Branch/worktree: feature/esp32-controller.
- Status: in progress; not integrated.
- Base commit: 70bc2cc.
- Resulting commits: 48ae9dd (skeleton), 3cbea5f (review feedback),
  0332bd9 (native build and parser fixes), f6213f3 (30-case shared vectors),
  1988d5a (merge of main for the request channel). Full detail is in
  docs/handoffs/esp32-controller.md.
- Changes and affected files/interfaces: firmware/ PlatformIO project with
  pure mecanum mixing, wheel wiring config, SessionGuard (arm/session/seq/
  500 ms watchdog), bounded LineReader, ArduinoJson protocol codec,
  Controller, and Motor Shield V2 / BNO055 adapters. The drive packet and
  drive ACK shapes are unchanged. Arm/disarm/arm_ack/disarm_ack/fault/imu
  shapes were defined by this workstream, and the Pi side adopted them.
- Reason and accepted design decisions:
  - A stale arm (same session, seq not higher than the last seen) is
    ignored; an arm in a new session is always accepted.
  - An accepted arm zeroes motor outputs.
  - Boot sends `{"type":"fault","reason":"boot","armed":false}`.
  - Unknown extra JSON fields are ignored. Duplicate keys are not rejected;
    ArduinoJson 6.21.6 keeps the last occurrence (measured).
  - `hardware_pwm_ceiling` stays 0 until physically validated.
- Tests (from firmware/, PlatformIO 6.2.0 in an isolated venv, Windows 11):
  - `pio test -e native` with GCC 15.2.0: 52/52 pass, including the 30
    shared serial vectors and boot_emit. This matches dashboard/control's
    independent 52/52 on macOS arm64.
  - `pio run -e esp32-s2`: SUCCESS, 0 warnings, RAM 4.8%, flash 20.6%.
  - A real defect was found and fixed along the way: a 256-byte
    StaticJsonDocument returned NoMemory for every valid drive packet (the
    firmware as of 3cbea5f could never have driven). Fixed in 0332bd9.
- Physical evidence: not performed. Nothing was flashed.
- Known failures or limitations: the board is a placeholder
  (esp32-s2-saola-1); wheel mapping, inversions, and ceiling are
  unvalidated; the Adafruit adapters have compiled but never run on
  hardware.
- Uncommitted work: none after this entry is committed.
- Coordination or merge notes: `AGENTS.md` and this file were edited on
  this feature branch at the user's explicit direction, overriding
  WORKSTREAMS.md's "do not modify from a feature branch" rule. Expect merge
  conflicts here with other branches. WORKSTREAMS.md still names
  `firmware/tests/`; the directory is `firmware/test/`.
- Next action: confirm the ESP32-S2 board and hardware facts, then Stage G
  from integrated main with the chassis raised.

### 2026-09-26 EDT - Dashboard buzzer and light toggles

- Workstream: full stack (dashboard, bridge, and firmware together).
- Stage: user-authorized exception; buzzer/LED was deferred polish.
- Branch/worktree: test/full-stack/feature-light-buzzer, based on
  test/full-stack d701c3b.
- Status: implemented and tested off-robot; not committed at time of writing.
- Hardware facts (from the user): NeoPixel Jewel 7 RGBW (SK6812), data in
  on QT Py RX = GPIO16; active buzzer on QT Py A3 = GPIO8, active high
  (assumed; verify). "On" for the light is all four channels at 255 on all
  seven pixels, up to ~0.5 A: power the Jewel from 5V.
- Interface decision (shared protocol extension, both sides updated on this
  branch): Pi -> ESP32 `{"type":"accessories","session","seq","buzzer",
  "light"}`, answered by `accessories_ack` with the same fields. Accepted
  armed or disarmed; a repeated or older seq in the same session is ignored;
  never refreshes the drive watchdog, arms, disarms, or counts as a motor
  ACK. Bridge IPC gains kind "accessories" and BridgeStatus.accessories.
  Browser message: `{"type":"accessory","name":"buzzer"|"light","on":bool}`.
- Behavior decision (user-chosen): only the owning browser can toggle; Stop
  does not change them; off at firmware boot, on a new serial session or
  control-service restart, at bridge or dashboard shutdown, and when the
  owning browser disconnects.
- Old firmware treats `accessories` as a malformed packet and disarms, so
  flash this firmware together with the matching Pi code.
- Shared vectors: 5 cases appended to fixtures/serial_protocol_vectors.json
  (and the byte-identical firmware copy), with an optional per-step
  `accessories` expectation checked by both harnesses. Existing cases are
  unchanged.
- Tests (Windows 11):
  - Firmware `pio test -e native` (GCC 15.2.0): 59/59 pass (56 before).
    A mutation that removed the stale-seq rule failed 4 vector checks.
  - `pio run -e esp32-s2`: SUCCESS, 0 warnings, RAM 8.6%, flash 21.8%,
    Adafruit NeoPixel 1.15.5. Not flashed.
  - `PYTHONPATH=app python -m unittest discover -s tests`: 156 run. The
    13 new tests pass except the Unix-socket bridge-backend test, which
    cannot run on Windows; it passed with in-memory datagrams in a scratch
    runner. No new failures beyond the Windows-only baseline (SIGINT and
    AF_UNIX), which also fails on unmodified HEAD.
  - Browser (mock backend, built-in browser): toggles confirmed by server
    state; toggling works armed; Space with a toggle focused still stops
    and does not toggle; a read-only second browser sees disabled buttons
    and its forged request is refused; closing the owner's tab turns both
    off; phone width has no horizontal overflow.
- Physical evidence: not performed. The buzzer polarity, Jewel color order,
  and current draw still need a bench check with the robot.
- Next action: flash the firmware and Pi code together, then bench-check
  both toggles on the robot with the chassis raised.

### 2026-09-27 EDT - Buzzer/light bench fixes and camera-driven automation

- Workstream: full stack (Pi dashboard, camera script, firmware), on the Pi.
- Branch/worktree: test/full-stack 97c0f05, ~/rescuebot-integration.
- Status: flashed and running on the robot; not committed at time of writing.
- Hardware facts (bench-checked with the user): the buzzer on A3 is
  passive, so a steady level was silent (A3 measured 0 -> 3.3 V). Firmware
  now drives a 2 kHz square wave on LEDC channel 0; `buzzer_duty_percent`
  (10, max 50) sets the volume. The Jewel stayed dark on 5V (measured 4.7 V,
  where 3.3 V data sits at the SK6812 logic threshold) and no level shifter
  was available, so it now runs from the QT Py 3V pin with `light_level` 48
  on all four channels (~100 mA). Data stays on RX = GPIO16. A wiring fault
  on the Jewel was then found and fixed by the user.
- Camera: ai_camera_detect.py adds libcamera's per-frame `Lux` estimate as
  an optional `lux` field on detection_frame records; DetectionFrame keeps
  it when present. Older records without it still parse.
- Behavior decision (user-requested): dashboard `--auto-accessories`
  (default off, or RESCUEBOT_AUTO_ACCESSORIES=1) runs AccessoryAutomation on
  its own thread. When a person first appears, the buzzer sounds for 2 s and
  the light flashes 5 times (0.2 s on/off), then returns to its previous
  state; no new alert until nobody has been seen for 3 s. Below 10 lux for
  5 s the light turns on; above 40 lux for 5 s it turns off. A dark/bright
  change acts like a Light press, so a manual press overrides it until the
  next change; turning the buzzer off ends a playing alert. Automation needs
  no owning browser. When the owner disconnects, their switches reset but a
  dark-scene light stays on (exception to the rule above). The Pi adopts
  the firmware's state after a reboot or new serial session. Never affects
  driving, arming, or watchdogs.
- Tests: `pytest` 188 passed, 2 skipped (15 new in
  tests/control/test_accessory_auto.py); firmware native 59/59;
  `pio run -e esp32-s2` SUCCESS, 0 warnings.
- Physical evidence: buzzer tone confirmed by the user; the Jewel lights
  after the wiring fix; first live alert logged at 162 lux (bright room).
- Next action: confirm the alert and the dark light by eye and ear; tune
  DARK_LUX/BRIGHT_LUX in app/rescuebot/accessory_auto.py if the light's own
  glow makes it toggle.

### 2026-09-27 EDT - Physical robot ROS 2 mapping (read-only)

- Workstream: full stack, branch test/full-product (worktree ~/rescuebot-full-product).
- Status: running on the Pi; user asked for physical autonomy, starting with
  ROS 2 on the Pi and real-LiDAR mapping while driving manually.
- New ROS package `ros_ws/src/rescuebot_robot` and slim Docker image
  `ros_ws/docker/robot/` (ROS 2 Jazzy ros-base + slam_toolbox; the Pi runs
  Debian 13, which has no ROS 2 packages). See ros_ws/docker/robot/README.md.
- Design decision: the ROS side reads the running dashboard's HTTP API
  (`/api/lidar`, `/api/state` IMU heading) instead of opening the LiDAR or
  ESP32 ports, so the dashboard keeps both and nothing in ROS can command
  motors. No wheel encoders: odometry carries only BNO055 yaw; slam_toolbox
  adds scans on a 0.4 s timer and scan matching finds the translation.
- Autonomy remains gated to `--motor-backend gazebo`; no drive path from ROS
  to the physical robot exists yet.
- Tests: rescuebot_robot conversions 8/8 (host and inside the image). Live:
  /scan ~3.7 Hz from the dashboard feed, map served on :8090, container
  ~10% CPU, dashboard unaffected.
- Physical evidence: first stationary map looked plausible. Scan
  orientation (clockwise bins mirrored to ROS counterclockwise) and IMU yaw
  sign still need a slow manual turn to confirm.
- Next action: drive slowly around the room and check the map stays sharp;
  then real person detections into ROS, Nav2 on the robot, and a gated drive
  path through the bridge with Stop and manual override on top.

### 2026-09-27 EDT - Physical autonomy behind --allow-physical-autonomy

- Workstream: autonomy (test/full-product)
- Stage: physical autonomy, pre-acceptance
- Branch/worktree: test/full-product, ~/rescuebot-full-product
- Status: in progress (code only; not run on the robot)
- Base commit: e25eead
- Changes and affected files/interfaces: dashboard flag `--allow-physical-autonomy`
  (env RESCUEBOT_ALLOW_PHYSICAL_AUTONOMY=1, bridge backend only) and
  `--autonomy-speed` (app/rescuebot/web.py). RobotControlService.autonomy_available
  (app/rescuebot/service.py) replaces the Gazebo-only checks, so Start autonomy,
  navigation_goal (move forward/right a set distance) and start_search work on the
  bridge when the flag is set. Autonomy sockets live in <run_dir>/ros so the ROS
  container never sees the bridge socket. ROS: rescuebot_robot autonomy.launch.py
  (mapping + Nav2 + Collision Monitor + mission manager + autonomy adapter),
  ros_ws/docker/robot/run_autonomy.sh; navigation.launch.py gains use_sim_time,
  slam and person_topic args; mission_manager ends a search on camera person
  sightings (/rescuebot/people).
- Reason and accepted design decisions: default stays manual-only. Stop, any held
  key, firmware disarm, stale bridge, or a silent ROS source cancel autonomy.
- Tests: `.venv/bin/python -m pytest -q tests` on the Pi: 232 passed, 2 skipped.
  ROS package tests need ROS 2 Jazzy (not run).
- Physical evidence: not performed. IMU unavailable on 2026-09-27 (shield I2C).
- Known failures or limitations: yaw-only odometry, no wheel encoders.
- Uncommitted work: none.
- Next action: dashboard with the flag, run_autonomy.sh, wheels-off-ground
  navigation_goal test, then floor test.

## Entry template

### <ISO date/time with timezone> - <task ID and title>

- Workstream:
- Stage:
- Branch/worktree:
- Status: in progress / ready for integration / integrated / blocked
- Base commit:
- Resulting commit, PR, or checkpoint: record when available
- Changes and affected files/interfaces:
- Reason and accepted design decisions:
- Tests: exact commands, outcomes, and environment
- Physical evidence: performed / not performed, configuration, observations
- Known failures or limitations:
- Uncommitted work:
- Coordination or merge notes:
- Next action:
