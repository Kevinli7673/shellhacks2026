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

## Entry template

### <ISO date/time with timezone> - <short task>

- Commit:
- Changed files and interfaces:
- Tests and results:
- Mock or physical coverage:
- Known limitations:
- Next action:
