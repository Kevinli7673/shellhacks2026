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

## Entry template

### <ISO date/time with timezone> - <short task>

- Commit:
- Changed files and interfaces:
- Tests and results:
- Mock or physical coverage:
- Known limitations:
- Next action:
