# Contributor and coding-agent instructions

## Start here

At the start of a task:

1. Read the current-state and active-work sections of changes.md.
2. Read the relevant sections of IMPLEMENTATION_PLAN.md.
3. Inspect the actual branch, working tree, and code before editing.
4. Confirm task ownership and interfaces that overlap other contributors.

Use docs/reference/rescue_robot_plan.md for original project context.
The approved implementation plan incorporates later user corrections.
Do not treat the original roadmap as authorization to expand scope.

Current explicit user instructions take precedence over repository guidance.
Record accepted changes to project decisions so future contributors inherit
them. Report discrepancies between documentation and code.

## Scope and architecture invariants

- Current milestone: manual driving, dashboard, person detection,
  annotated video, recording/replay, firmware, and tests.
- No LiDAR, SLAM, autonomous navigation, localization, hazard mapping,
  A*, C6 display work, or unrelated polish before physical acceptance.
- ROS 2 is optional and cannot block initial driving.
- ESP32-S2 alone controls the motor shield and reads BNO055.
- Real mecanum mixing belongs on ESP32-S2.
- Keep the exact approved mixing equations.
- Correct physical wheel direction through configuration, not equation edits.
- Positive axes mean forward, right strafe, and clockwise rotation.
- Camera processing remains isolated from motor-control processes.
- Stop overrides everything; reconnects never automatically rearm.
- ESP32 watchdog defaults to 500 ms.
- Mock and firmware calculations must pass the same behavioral fixtures.
- Replay must never drive real motors.

## Concurrent work

Use one task branch and separate worktree/clone per concurrent contributor.
Do not let independent agents edit the same working tree simultaneously.

Agree on task ownership before editing overlapping files.
Record owner, branch/worktree, affected areas, dependencies, and status
in changes.md. The Markdown table is a record, not a distributed lock:
confirm assignments with the coordinating human or agent.

Keep changes focused on the assigned task.
Do not overwrite, discard, stage, or commit another contributor's changes.

Coordinate edits to shared protocols, dependencies, configuration,
mixing fixtures, and documentation before making incompatible changes.
One integration owner merges completed work sequentially and reconciles
the shared current-state summary. The contributor integrating a branch
acts as integration owner for that merge.

When resolving documentation conflicts, preserve both contributors'
distinct history entries and recompute the current summary from evidence.

Do not force-push shared branches or rewrite another contributor's history.
Use a new fix/revert commit when an integrated change needs correction.

## Checkpoints and validation

Keep the last passing checkpoint available.
Commit small coherent changes with relevant tests and documentation.

For each checkpoint, record:

- Stage and scope.
- Commit/tag identifying the tested version.
- Exact validation commands and outcomes.
- Environment and mock versus physical coverage.
- Known limitations and remaining acceptance tests.

Do not move an existing checkpoint tag to a different commit.
Do not claim that tests ran when they did not.
A mock pass or firmware build is not a physical robot pass.

Prefer simple solutions and existing dependencies.
Keep IPC bounded, nonblocking, expiring, and process-isolated.
Avoid unrelated refactors and speculative infrastructure.

## Handoff requirements

Before ending a work session or handing off:

- Update changes.md with changes, rationale, test evidence, and blockers.
- State whether work is committed, uncommitted, or integrated.
- Identify affected interfaces and compatibility changes.
- Give a concrete next action and reproduction steps for failures.
- Update IMPLEMENTATION_PLAN.md when an accepted design decision changes.
- Update README when setup, run, flash, or test commands change.

Keep AGENTS.md concise and stable.
Put chronological history in changes.md, not here.
Do not rely on chat history as the only record of an important decision.

Do not commit secrets, private connection details, bulk recordings,
downloaded model weights, or generated build artifacts by default.
Small intentional test fixtures are appropriate.
