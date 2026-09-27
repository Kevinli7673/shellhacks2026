# Physical acceptance runbook

This runbook turns the physical acceptance list in IMPLEMENTATION_PLAN.md
section 11 into steps with pass criteria. Run it on the integrated build
(test/integration), with the ESP32-S2 connected to the Raspberry Pi.

A mock pass or a firmware build is not a physical pass. Record every result,
including failures, in the results table at the end.

## Roles

- **Operator**: drives from the browser and watches the dashboard.
- **Spotter**: watches the robot, keeps a hand near the motor power switch,
  and films timing checks.
- **Recorder**: fills in the results table (can be the operator).

The spotter can cut motor power at any time. Stop the run on any unexpected
movement.

## 0. Preconditions

1. Firmware: the flashed build's commit is recorded. It boots disarmed, sends
   a `boot` fault, then streams `imu` lines at 20 Hz.
2. Wiring: `firmware/include/chassis_config.h` holds the verified mapping
   (FL=M3, FR=M1 inverted, RL=M4, RR=M2 inverted). `hardware_pwm_ceiling`
   is 60 until floor tests justify raising it.
3. The ESP32-S2 connects to the Pi with a USB data cable. Find its stable
   path. Use the QT Py/Adafruit entry. The CP2102N entry is the LiDAR;
   never use it.

       ls -l /dev/serial/by-id/

4. Start the bridge and dashboard from the repository root on the Pi:

       .venv/bin/python -m rescuebot.motor_bridge \
         --transport serial --serial-device /dev/serial/by-id/<qtpy-entry>
       .venv/bin/rescuebot-dashboard --motor-backend bridge --camera-backend live

5. Check the state from any machine on the network:

       curl -s http://<pi-address>:8000/api/state

   Expect `motor.healthy` true, `motor.transport_connected` true,
   `motor.firmware_armed` false, and `motor.imu.available` true.
6. First pass on a raised chassis (wheels off the ground). Repeat sections
   1-3 on the floor with at least 1 m of clear space.

## 1. Driving controls

Click Enable Driving before each test unless the test says otherwise.
Expect "Arming…" and then "Armed" within 1 s.

| ID | Action | Pass when |
|---|---|---|
| D1 | Hold W, then S | Robot moves toward the camera end, then away from it |
| D2 | Hold A, then D | Robot strafes left, then right, without turning |
| D3 | Hold Left arrow, then Right arrow | Robot rotates counterclockwise, then clockwise (seen from above) |
| D4 | Hold W+D, W+Right arrow, and W+S | Diagonal and arcing moves; W+S produces no forward/back motion |
| D5 | Press Up/Down arrow (speed ±10%) with no movement key held | No wheel moves; the dashboard wheel values stay 0 |
| D6 | Press Up/Down arrow while holding W | Speed changes, direction does not |
| D7 | Release all movement keys while moving | Wheels stop; driving stays enabled |
| D8 | Refresh the page, then press W without clicking Enable | Nothing moves |

## 2. Stop and fault shutdown

Each test starts while holding W with driving enabled. After each one,
confirm that pressing W again does nothing until Enable Driving is clicked.
This covers "reconnection never resumes movement".

| ID | Action | Pass when | Dashboard reason |
|---|---|---|---|
| S1 | Press Space | Wheels stop at once and driving is disabled | `operator_stop` |
| S2 | Click another window (blur) | Wheels stop | `operator_stop` (the browser sends Stop on blur) |
| S3 | Switch browser tab | Wheels stop | `operator_stop` |
| S4 | Turn off the operator laptop's Wi-Fi for 3 s, then back on | Wheels stop within 0.5 s; no motion after reconnect | `browser_timeout` or `browser_disconnected` |
| S5 | Kill the dashboard process (`kill <pid>`), then restart it | Wheels stop within 0.5 s; no motion after restart | bridge `arbiter_timeout` |
| S6 | Kill the motor bridge process, then restart it | Wheels stop within 0.5 s; no motion after restart | `backend_unavailable` |
| S7 | Unplug the ESP32-S2 USB cable, then replug it | Wheels stop within 0.5 s; firmware reboots disarmed | `link_lost`, then `boot` |
| S8 | Press RESET on the ESP32-S2 | Wheels stop; the dashboard reports the reboot | `boot` |

The 0.5 s bound comes from the ESP32 watchdog (500 ms). The Pi-side deadlines
are 250 ms.

## 3. Firmware safety (serial)

Run these from the Pi with the bridge stopped, so only the test script owns
the port. The chassis is raised and the motor power is on.

| ID | Action | Pass when |
|---|---|---|
| F1 | Send malformed lines, oversized lines, and drive commands with the wrong session or a stale seq | No wheel moves; faults are `malformed_packet` or `oversized_packet`; stale and wrong-session drives get no ACK |
| F2 | Arm, drive forward for 1 s, then stop sending | `watchdog_expired` fault arrives 500-600 ms after the last command; wheels stop |
| F3 | Send drive commands without arming | No ACK, no wheel movement |

For F2, log the time of the last command sent and the time the fault arrives.
The difference is the measured firmware cutoff. Record it.

## 4. Multiple browsers

| ID | Action | Pass when |
|---|---|---|
| M1 | Open the dashboard in a second browser while the first is driving | The second is read-only; its keys do nothing |
| M2 | Close the first browser | Robot stops; the second browser must click Enable before it can drive |

## 5. Camera and detections

| ID | Action | Pass when |
|---|---|---|
| C1 | Stand 1-3 m in front of the AI Camera under venue lighting | A person is detected within 2 s |
| C2 | Walk across the view | The box follows the person and stays aligned with them in the video |
| C3 | Leave the view | The box disappears; no stale box stays on screen |
| C4 | Stop the camera process (`kill` the `ai_camera_detect.py` child) while driving | The dashboard shows the camera as failed; driving and Stop keep working |
| C5 | Watch the video for 5 minutes | Delay does not grow; a hand wave appears in under 1 s each time |

To record detections for replay, run the detector alone. The dashboard's
live camera must not be running at the same time, since only one process can
own the camera.

    .venv/bin/python ai_camera_detect.py --json --headless --only person > run.jsonl
    .venv/bin/rescuebot-dashboard --motor-backend mock --camera-backend replay --replay-path run.jsonl

| ID | Action | Pass when |
|---|---|---|
| R1 | Replay a recording | Boxes replay at the original pace; no motor command is sent |

## 6. Endurance

| ID | Action | Pass when |
|---|---|---|
| E1 | 15 minutes of continuous driving with the live camera and video on | No unexpected stop or fault; no firmware hang; video delay stays flat |

During E1, poll `/api/state` every few seconds. `motor.ack_age_ms` should stay
under 250 ms, and `motor.imu` should keep updating.

## 7. Stopping measurements

Film S1, S5, and S7 with a phone in slow motion (240 fps if available). Keep
the wheels and the dashboard (or the unplugged cable) in frame. Count frames
from the action to the wheels stopping. At 240 fps, one frame is about 4 ms.
Motor stop means output off, not braking, so also note the coast distance on
the floor.

## Results

| ID | Date/time | Firmware commit | App commit | Raised/floor | Result | Measured value | Notes |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

A test passes only if its "Pass when" column is fully met. Record partial
results as failures, with notes.
