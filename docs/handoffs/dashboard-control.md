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
- Detection recording/replay and a replay camera backend (mock/replay
  camera_backend) are merged and tested with fixtures only.
- The Pi-side serial protocol codec, MotorLink session/ACK logic, and a
  simulated firmware are merged; they follow the ESP32 firmware's message
  format but are not yet wired to the dashboard.
- The motor-bridge process, real serial transport, live camera, and video
  are not implemented.

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

### 2026-09-26 EDT - Reviewed detection replay and replay-camera backend

- Commits and branch state:
  - Reviewed feature/detection-replay commit 179c067 and added review fix
    f6eae78, `fix: keep detection recording shutdown nonblocking`.
  - Fast-forwarded feature/detection-replay into feature/dashboard-control at
    f6eae78. The merged dashboard branch passed its full test suite.
  - Created feature/replay-camera-backend from that merged branch and added
    9251279, `feat: add replay camera backend`.
- Review findings and frozen-interface decisions:
  - Fixed DetectionRecorder shutdown: a full queue previously allowed close()
    to block and could race a concurrent submit(). Close now remains bounded,
    and no submit is accepted after closing starts.
  - Detection image dimensions currently serialize as
    `image: {width, height}`. This is used consistently by the implementation,
    but the frozen event interface should be explicitly confirmed by the
    integrator before adding external producers.
  - `tests/control/` is the assigned dashboard/control test location; no test
    path change is needed.
  - `offline` means no frame has ever reached DetectionTracker. A replay
    process that ends or fails after a received frame becomes `stale` once the
    one-second receipt-time expiry passes; `process_alive` remains available
    as separate health context.
- Changed files and interfaces in 9251279:
  - Added replay_camera.py with a separate replay process, bounded size-one
    latest-frame IPC, receipt-time DetectionTracker status, and mock fallback.
  - The dashboard API and browser display camera status, count, normalized
    bounding boxes, and a REPLAY label. Camera polling is separate from the
    20 Hz control loop.
  - `rescuebot-dashboard --camera-backend replay --replay-path <file>` selects
    a JSONL recording; mock/offline remains the default.
- Tests and results:
  - `.venv/bin/python -m unittest discover -s tests -v`: 46 passed.
  - `node --check app/rescuebot/static/dashboard.js`: passed.
  - `.venv/bin/python -m py_compile app/rescuebot/replay_camera.py app/rescuebot/web.py`:
    passed.
  - `.venv/bin/rescuebot-dashboard --help`: passed.
  - Replay backend lifecycle smoke test: passed.
- Mock or physical coverage:
  - JSONL fixture replay and mock only. No physical camera, IMX500 model,
    Raspberry Pi, motor hardware, or annotated video stream was used.
- Known limitations:
  - Live capture/inference and video streaming remain intentionally deferred.
  - The replay process feeds detections only; it has no motor-control path.
- Next action:
  - Review and merge 9251279 from feature/replay-camera-backend into
    feature/dashboard-control. Keep the image-dimension container decision
    synchronized before external camera providers are introduced.

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

### 2026-09-26 EDT - Align Pi-side serial link with ESP32 firmware messages

- Commit: feature/serial-protocol; fix: align serial link with firmware
  messages. Supersedes the "Proposed interface additions" in the previous
  entry.
- Changed files and interfaces:
  - serial_protocol.py now parses the firmware's replies from
    feature/esp32-controller 48ae9dd: arm_ack/disarm_ack
    {type, session, seq, armed}, fault {type, reason, armed:false} with
    reasons malformed_packet, oversized_packet, watchdog_expired, and imu
    {type, timestamp_ms, available, heading?, calibration?}. The "state"
    message is removed. MAX_LINE_BYTES is now 200 to match the firmware
    LineReader.
  - serial_link.py arms only on an arm_ack matching its pending arm seq,
    answers unrequested or foreign-session arm_acks with a disarm, disarms
    on any fault, and detects reboots from an IMU timestamp reset or,
    at the latest, the 250 ms ACK deadline (the firmware sends no boot
    message).
  - serial_sim.py mirrors the firmware's current behavior, including arm
    adopting any session and faults being sent even while disarmed.
  - fixtures/serial_protocol_vectors.json regenerated: 26 cases in the
    firmware format. Cases still under discussion (stale arm, extra or
    duplicate fields, boot message, re-arm output reset) are omitted.
  - Drive packet, drive ACK, and arm/disarm commands are unchanged.
- Tests and results:
  - PYTHONPATH=app ../shellhacks2026/.venv/bin/python -m unittest discover
    -s tests: 67 passed.
  - Mutation checks (link ignoring faults, unrequested or foreign arm,
    IMU reset, ACK deadline, sim driving after malformed input, 256-byte
    line limit) each made the suite fail.
  - The firmware's own C++ tests had not yet been compiled or run.
- Mock or physical coverage:
  - Simulated firmware only. The firmware's C++ was not compiled here.
- Known limitations:
  - Parity with the firmware is by code reading until the firmware runs
    these vectors in its native test suite.
  - Change requests for the ESP32 workstream are pending (stale-arm
    rejection, boot message, re-arm output reset, extra/duplicate field
    policy).
- Next action:
  - After the firmware's native tests pass, have the firmware run
    fixtures/serial_protocol_vectors.json, then record the agreed
    messages as a changes.md decision during integration.

### 2026-09-26 EDT - Merge replay camera backend and serial protocol

- Commit: merge of feature/replay-camera-backend (fast-forward to 4c51298)
  and feature/serial-protocol (b23d95e) into feature/dashboard-control.
  feature/detection-replay was already contained at f6eae78. Not merged:
  main and feature/esp32-controller (other workstreams).
- Changed files and interfaces:
  - No code conflicts. The only conflict was this handoff log, where both
    branches appended entries; all entries are kept in chronological order
    and Current state was recomputed.
- Tests and results:
  - .venv/bin/python -m unittest discover -s tests: 71 passed
    (control 13, detections 20, mecanum 8, mecanum fixtures 1,
    replay camera 4, serial protocol 25), matching both branches'
    pre-merge totals (46 and 67, sharing 42).
  - node --check app/rescuebot/static/dashboard.js: passed.
  - Correction: the earlier serial-protocol entry's "41 existing, 23 new"
    should read "42 existing, 22 new"; f6eae78 had added one test.
- Mock or physical coverage:
  - Mock, fixture replay, and simulated firmware only.
- Known limitations:
  - ESP32 change requests and the detection image-dimension decision are
    still open; a1vcm's ai_camera_detect.py on main emits a different
    detection shape (flat width/height, integer camera_id, nanosecond
    timestamp) that our loader does not accept yet.
- Next action:
  - Motor-bridge process around MotorLink using the simulated firmware.

### 2026-09-26 EDT - Match firmware 3cbea5f and verify C++ parity

- Commit: feature/dashboard-control; fix: match firmware arm and boot
  behavior.
- Changed files and interfaces:
  - serial_sim.py follows firmware 3cbea5f: sends
    {"type":"fault","reason":"boot","armed":false} on reboot, ignores a
    stale arm (same session, seq not newer), and zeroes outputs on any
    accepted arm.
  - parse_command ignores unknown extra fields, matching the firmware's
    agreed policy. The firmware reads the first of duplicate keys; that
    case is not covered by the vectors.
  - MotorLink treats the boot fault as an immediate disarm (fault "boot");
    the IMU-reset and ACK-deadline fallbacks remain for a lost boot line.
  - fixtures/serial_protocol_vectors.json: 30 cases. Updated the re-arm
    case, added stale-arm, same-session re-arm, arm-after-disarm, and
    extra-field cases, and a top-level boot_emit.
- Tests and results:
  - .venv/bin/python -m unittest discover -s tests: 72 passed.
  - Firmware 3cbea5f, in a scratch copy with the PlatformIO CLI
    (pip install platformio; pio test -e native): as pushed, all four
    suites fail to link. With test_build_src = yes,
    -D UNITY_INCLUDE_DOUBLE, kJsonCapacity 512, and the codec test's
    StaticJsonDocument<128> raised to 256, 52/52 pass, including
    test_protocol_fixtures against these 30 vectors.
- Mock or physical coverage:
  - Python simulation and the firmware's native (host) build only. No
    ESP32-S2 build, board, or motors.
- Known limitations:
  - The four firmware fixes above are not yet on feature/esp32-controller;
    kJsonCapacity 256 rejects every valid drive packet as malformed.
  - The firmware's copy of the vectors is at the previous 26-case version.
- Next action:
  - After the firmware applies the fixes and copies the 30-case vectors,
    record the agreed serial messages as a changes.md decision during
    integration.

### 2026-09-26 EDT - Motor-bridge process and arbiter IPC (step 1)

- Commit: branch feature/motor-bridge from feature/dashboard-control
  (e101f82); feat: add motor bridge process and arbiter IPC.
- Changed files and interfaces:
  - Added app/rescuebot/bridge_ipc.py: nonblocking Unix datagram IPC.
    Arbiter commands are {kind: drive|stop|arm, arbiter, seq, expires_at
    (time.monotonic), forward, sideways, turn, speed_limit}. Senders drop
    and count instead of blocking. Receivers drain a bounded batch; a stop
    discards drives received before it, and only the newest drive is kept.
    The bridge reports BridgeStatus datagrams (armed, arm_pending, fault,
    ack age, wheels, IMU) back to the service.
  - Added app/rescuebot/motor_bridge.py: MotorBridge around MotorLink.
    It sends drive packets at 20 Hz from the newest fresh command only, and
    disarms after 250 ms without a fresh arbiter command (receipt age or
    expiry) or an advancing ACK. Stop is handled in the same step. It
    arms only on an explicit "arm" plus the firmware arm_ack, disarms on a
    new arbiter id, rejects replayed seqs, disarms if the transport closes,
    and reconnects with a new session at most once per second without
    re-arming. SIGTERM/SIGINT send a final disarm before exit.
  - Only a simulated-firmware transport exists: `python -m
    rescuebot.motor_bridge [--command-socket P] [--status-socket P]`.
    Default sockets live under /tmp/rescuebot-<uid>/ (RESCUEBOT_RUN_DIR).
  - Control-service contract for step 2: send current intent every tick,
    "drive" while armed or while waiting for arm_ack (zero axes while
    waiting), "stop" otherwise. A "stop" cancels a pending arm.
  - No changes to service.py, control.py, web.py, mock.py, the serial
    protocol, or mixing.
- Tests and results:
  - .venv/bin/python -m unittest discover -s tests: 92 passed; 25 and 10
    repeated runs all passed after fixes.
  - tests/control/test_motor_bridge.py includes a separate-process test
    over real Unix sockets (arm, drive to 80, stop, SIGTERM exit 0).
  - Mutation checks (no arbiter timeout, accepting expired or replayed
    commands, ignoring stop, arbiter change, dead transport, 20 Hz cadence,
    stop not dropping an earlier drive) each made the suite fail.
  - Bugs found and fixed while testing: a transport closed without an
    error left the bridge reporting armed; SIGTERM skipped the final
    disarm.
- Mock or physical coverage:
  - Simulated firmware only. No serial port, pyserial, ESP32, or motors.
- Known limitations:
  - Not yet used by the dashboard; the control service still drives the
    in-process mock backend.
  - No real serial transport yet (pyserial is installed on the Pi but is
    not a project dependency).
  - macOS local datagram buffers are 4 KB; drops are expected under stall
    and repaired by per-tick resend.
- Next action:
  - Step 2: add motor_backend = mock | bridge to the control service, send
    arbiter commands each tick, gate Enable on the bridge's arm_ack
    status, and show bridge health on the dashboard.
  - Step 3: pyserial transport opened by /dev/serial/by-id path.

## Entry template

### <ISO date/time with timezone> - <short task>

- Commit:
- Changed files and interfaces:
- Tests and results:
- Mock or physical coverage:
- Known limitations:
- Next action:
