# Project status, changes, and handoffs

This file records current progress and evidence.
IMPLEMENTATION_PLAN.md defines the approved design.
AGENTS.md defines contributor rules.

Keep the current-state section concise.
Append dated entries to History; preserve previous entries.
Correct historical mistakes with a new entry rather than silently erasing them.

## Current state

- Milestone: manual driving + person detection + dashboard.
- Design: approved for implementation.
- Implementation: documentation baseline in progress.
- Stages A-K: not started.
- Stage L: deferred until physical milestone acceptance.
- Physical acceptance: not performed.
- Known application checkpoint: none.
- Initial repository baseline: 34fb0b2, "Initial commit".
- Initial tracked content: README.md.
- Next action: commit the documentation baseline, then begin Stage A.

## Active work

| Task/stage | Owner | Branch/worktree | Affected areas/interfaces | Status | Updated |
|---|---|---|---|---|---|
| Documentation baseline | Current implementation session | main | Plan, contributor rules, handoff log, original reference, README | In progress | 2026-09-26 EDT |

Assignments must be confirmed with the team before overlapping work begins.
This table alone does not lock files or synchronize separate branches.

## Checkpoints

| Reference | Coverage | Validation evidence | Limitations |
|---|---|---|---|
| 34fb0b2 | Initial repository | Repository inspection only | No application or hardware validation |

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

Status: in progress.

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
- Concurrent contributors use separate branches/worktrees and explicit ownership.

Validation:

- Repository and original project plan inspected.
- No code tests, firmware tests, or physical tests performed.

Next:

- Commit the documentation baseline.
- Begin Stage A, followed by the approved stage sequence.

## Entry template

### <ISO date/time with timezone> - <task ID and title>

- Contributor:
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
