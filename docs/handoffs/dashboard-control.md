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

## Entry template

### <ISO date/time with timezone> - <short task>

- Commit:
- Changed files and interfaces:
- Tests and results:
- Mock or physical coverage:
- Known limitations:
- Next action:
