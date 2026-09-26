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
- Implementation: documentation baseline complete; application code not started.
- Stages A-K: not started.
- Stage L: deferred until physical milestone acceptance.
- Physical acceptance: not performed.
- Known application checkpoint: Stage 0 documentation baseline (tag: stage-0-docs).
- Initial repository baseline: 34fb0b2, "Initial commit".
- Initial tracked content: README.md.
- Next action: start the dashboard/control and ESP32-controller workstreams.

## Active work

| Workstream | Branch/worktree | Affected areas/interfaces | Status | Updated |
|---|---|---|---|---|
| Documentation baseline | main | Plan, repository rules, handoff log, original reference, README | Complete | 2026-09-26 EDT |
| Dashboard and control | feature/dashboard-control | app/, static assets, control tests, mock backend | Planned | 2026-09-26 EDT |
| ESP32 controller | feature/esp32-controller | firmware/, firmware tests, shield and IMU adapters | Planned | 2026-09-26 EDT |

See WORKSTREAMS.md for the assigned development split and merge gates.
This table alone does not lock files or synchronize separate branches.

## Checkpoints

| Reference | Coverage | Validation evidence | Limitations |
|---|---|---|---|
| 34fb0b2 | Initial repository | Repository inspection only | No application or hardware validation |
| stage-0-docs | Documentation baseline | Original plan copied byte-for-byte; repository documentation inspected | No application or hardware validation |

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
