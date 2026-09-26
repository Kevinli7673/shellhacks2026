# Dashboard and control handoff

Workstream: dashboard and control  
Branch: feature/dashboard-control  
Status: ready for integration after review

This workstream owns the files listed in WORKSTREAMS.md.
Do not edit shared project documents while parallel work is active.

## Current state

- Base checkpoint: stage-0-docs.
- FastAPI dashboard, mock motor backend, and browser control loop are implemented.
- Stages A-C and the browser/control-service portion of Stage D have passing tests.
- Serial transport, the separate motor-bridge process, recording/replay, and
  camera integration are intentionally not implemented in this checkpoint.

## Local run

Create a local environment and start the dashboard:

    python3 -m venv .venv
    .venv/bin/pip install -e .
    .venv/bin/python -m uvicorn rescuebot.web:app --host 0.0.0.0 --port 8000

Open http://localhost:8000. The active backend is mock only.
Never treat the displayed wheel values as a command to real hardware.

## Raspberry Pi environment

Read-only inspection on 2026-09-26 EDT. Nothing was installed or changed.
Private details (username, network, device serial numbers) are omitted.

- Hardware/OS: Raspberry Pi 5 Model B Rev 1.1, 8 GB RAM; Debian 13
  (trixie), kernel 6.18 aarch64; not throttled; ~200 GB free.
- Python: system /usr/bin/python3 3.13.5 (project requires >= 3.11).
  Installed system-wide: picamera2 (python3-picamera2 0.3.37), OpenCV
  4.10.0, NumPy 2.2.4, pyserial 3.5. FastAPI is not installed; use a venv.
- Camera stack: imx500-all 1.13.0, imx500-firmware, imx500-models,
  imx500-tools, libcamera 0.7.2, rpicam-apps 1.13.0 with
  imx500-postprocess.
- IMX500 detection models present in /usr/share/imx500-models/ include
  yolo11n_pp, ssd_mobilenetv2_fpnlite_320x320_pp,
  efficientdet_lite0_pp, and nanodet_plus_416x416_pp. No model has been
  selected yet.
- Camera: rpicam-hello --list-cameras reported "No cameras available!"
  with no camera process running. Cause not yet diagnosed (cable, port,
  or config.txt). Blocks camera work, not driving.
- Serial: /dev/ttyUSB0 is a Silicon Labs CP2102N USB-UART bridge. The
  original plan lists the RPLIDAR C1 with a CP210x adapter, so this is
  likely the LiDAR, not the ESP32-S2. No /dev/ttyACM* device was present.
  The user is in the dialout group. The motor bridge must open the ESP32
  by its /dev/serial/by-id/ path, never a bare ttyUSB/ttyACM name.
- Running software: no Python or camera processes; Docker is installed
  and running but unused by this project.
- Existing scripts in the home directory (not in this repository):
  ai_camera_detect.py, detect.py, detect_fast.py, cam_test.py, app.py,
  a yolo11n NCNN model folder, and LiDAR test scripts (out of scope).
- Status confirmed by the team: the ESP32-S2 was not connected (still
  being breadboarded/soldered), and the camera hardware was being worked
  on at inspection time.
- Open questions: ESP32-S2 USB identity (native USB CDC vs. a CP210x
  bridge), camera connection/config, and contents of ai_camera_detect.py.
  Re-run the read-only camera and serial checks once each is connected.

## Handoff log

### 2026-09-26 EDT - Dashboard/control mock checkpoint

- Commit: 5138eaf, feat: add mock manual control dashboard.
- Changed files and interfaces:
  - Added app/rescuebot/ with pure mecanum mixing, manual control safety,
    mock backend, FastAPI transport, and browser dashboard assets.
  - Added fixtures/mecanum_vectors.json as the shared Python/firmware parity
    fixture, plus standard-library tests under tests/control/.
  - Browser protocol currently supports claim, enable, keys, speed, and stop.
- Tests and results:
  - .venv/bin/python -m unittest discover -s tests -v: 22 passed.
  - FastAPI import, JavaScript syntax, HTTP routes, and a localhost WebSocket
    ownership/arming/timeout scenario passed.
- Mock or physical coverage:
  - Mock only. No serial, firmware, Pi, camera, or motor hardware was used.
- Known limitations:
  - No serial backend or independent bridge process yet.
  - Dashboard has a camera-offline mock state only.
  - No recording/replay or detection event pipeline yet.
- Next action:
  - Add the bounded, latest-command-only motor-bridge IPC contract without
    changing browser controls or the frozen serial/mixing interfaces.

### 2026-09-26 EDT - Model-agnostic detection recording and replay

- Commit: branch feature/detection-replay, based on 8480650;
  feat: add detection recording and replay.
- Changed files and interfaces:
  - Added app/rescuebot/detections.py: BoundingBox, Detection, and
    DetectionFrame for the approved event shape (timestamp, frame_id,
    camera_id, image width/height, detections with label, confidence, and
    normalized top-left x/y/width/height). Includes pixel-to-normalized
    conversion, the 0.5 default confidence filter, and DetectionTracker
    (offline before any frame, online within 1 s, stale afterwards; stale
    and offline status carry no detections).
  - Added app/rescuebot/detection_replay.py: JSONL records of type
    "detection_frame" (image dimensions stored as image.width/height),
    a nonblocking bounded DetectionRecorder that counts dropped frames,
    and replay_frames, which delivers frames at recorded intervals with
    rebased timestamps and preserves original_timestamp as metadata.
    Replay only produces detection frames and imports no motor code.
  - Added fixtures/detections/ for appearance/disappearance, empty frames,
    overlapping people, and a stale gap/end-of-stream case.
  - Added tests/control/test_detections.py. No existing modules,
    dashboard, serial protocol, or mixing code changed.
- Tests and results:
  - .venv/bin/python -m unittest discover -s tests -v: 41 passed
    (22 existing, 19 new); 30 repeated runs passed.
- Mock or physical coverage:
  - Recorded fixtures and a fake clock only. No camera, IMX500 model, Pi,
    or dashboard integration was exercised.
- Known limitations:
  - Not wired into the dashboard, camera service, or a camera_backend
    setting; no live provider or annotated video.
  - Recording file rotation/size limits and the dashboard display of
    dropped-entry counts are not implemented.
  - Offline means "no frame received yet"; a separate camera-health
    signal is left to the camera service integration.
- Next action:
  - Review and merge into feature/dashboard-control, then add a
    replay camera_backend that feeds DetectionTracker status to the
    dashboard in a process isolated from motor control.

### 2026-09-26 EDT - Pi-side serial protocol and simulated link failures

- Commit: branch feature/serial-protocol (worktree ../rescuebot-serial),
  based on f6eae78; feat: add Pi-side serial protocol and link simulation.
- Changed files and interfaces:
  - Added app/rescuebot/serial_protocol.py: strict newline-delimited JSON
    codec for the frozen drive packet and ACK, a bounded LineDecoder
    (256-byte lines), and rejection of non-finite, out-of-range,
    non-integer, missing/extra/duplicate-key, and oversized input.
  - Added app/rescuebot/serial_link.py: transport-agnostic MotorLink with a
    fresh session per connect, monotonically increasing seq, no
    retransmission, explicit arm confirmation, 250 ms advancing-ACK
    deadline, and stale/foreign ACK rejection. IMU messages never refresh
    the deadline. Connect sends only a disarm and never arms.
  - Added app/rescuebot/serial_sim.py: host simulation of the firmware
    behavior (500 ms watchdog, hardware ceiling, shared mix_mecanum).
  - Added fixtures/serial_protocol_vectors.json (25 cases) for firmware
    parity, and tests/control/test_serial_protocol.py.
  - No existing module, the frozen drive/ACK shapes, or mixing changed.
- Proposed interface additions (need ESP32-workstream agreement and a
  changes.md decision before they are frozen):
  - Pi -> ESP32: {"type":"arm"|"disarm","session","seq"}, sharing the
    drive seq counter. Disarm always stops and adopts its session; arm
    requires the current session and a newer seq.
  - ESP32 -> Pi: {"type":"state","session","armed","ack","fault"} in reply
    to arm/disarm (ack = that seq) and unprompted on boot (session null,
    fault "boot"), watchdog ("watchdog"), and malformed input
    ("malformed"). IMU uses {"type":"imu",...}; the untyped message
    remains the frozen drive ACK.
  - Sessions are 1-32 characters of [A-Za-z0-9_-]; seq starts at 1.
  - Firmware ignores (no ACK, no watchdog refresh) wrong-session,
    duplicate, out-of-order, and disarmed drive packets; it stops and
    disarms on any malformed line.
- Tests and results:
  - PYTHONPATH=app ../shellhacks2026/.venv/bin/python -m unittest discover
    -s tests: 64 passed (41 existing, 23 new). PYTHONPATH is needed
    because the shared venv's editable install points at the main checkout.
  - Mutation checks (stale ACK acceptance, IMU refreshing the deadline,
    unrequested arm, duplicate seq, late watchdog, silent connect) each
    made the suite fail.
- Mock or physical coverage:
  - Simulated firmware only. No serial port, pyserial, ESP32, or motors.
- Known limitations:
  - No real serial transport, motor-bridge process, or arbiter IPC yet;
    nothing calls MotorLink from the dashboard.
  - Vectors are a proposal until the firmware passes them.
- Next action:
  - Share fixtures/serial_protocol_vectors.json and the proposed messages
    with the ESP32 workstream; after agreement, add the motor-bridge
    process with a pyserial transport around MotorLink.

## Entry template

### <ISO date/time with timezone> - <short task>

- Commit:
- Changed files and interfaces:
- Tests and results:
- Mock or physical coverage:
- Known limitations:
- Next action:
