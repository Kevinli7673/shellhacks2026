# Parallel workstream plan

This document divides the approved milestone between two developers without
overlapping implementation files. It does not change the approved architecture,
safety requirements, or definition of done in IMPLEMENTATION_PLAN.md.

Do not add attribution lines to source files, commit messages, or
documentation. Use ordinary Git commit metadata only.

## Worktree setup

Start both branches from the immutable Stage 0 checkpoint:

    git fetch --tags
    git worktree add ../rescuebot-dashboard -b feature/dashboard-control stage-0-docs
    git worktree add ../rescuebot-esp32 -b feature/esp32-controller stage-0-docs

Separate clones can use the same branch names instead of worktrees.
Never develop both workstreams in the same checkout.

## Assigned work

| Workstream | Branch | GitHub handoff | Primary paths | Approved stages |
|---|---|---|---|---|
| Dashboard and control | feature/dashboard-control | Primary developer | app/, tests/control/, fixtures/, docs/handoffs/dashboard-control.md | A, B, C, D, dashboard side of E, H-K |
| ESP32 controller | feature/esp32-controller | imatthew2008 | firmware/, firmware/tests/, docs/handoffs/esp32-controller.md | firmware side of E, F, hardware support for G |

The dashboard/control workstream owns:

- FastAPI dashboard, browser keyboard handling, connection ownership,
  Stop and Enable Driving flows, and health/status UI.
- The command arbiter, latest-command IPC sender, mock motor backend,
  recording/replay, detection-dashboard integration, and Python tests.
- Shared mecanum behavior fixtures after the first mixing implementation.
- Camera service integration after the driving stack is stable.

The ESP32-controller workstream owns:

- Arduino project structure, bounded serial parser, session/sequence checks,
  arm/disarm behavior, and 500 ms configurable watchdog.
- Exact mecanum mixing, motor-shield adapter, wheel mapping/inversions,
  bounded I2C access, and BNO055 telemetry.
- Firmware tests or host-side fixtures and raised-chassis hardware validation.

## Frozen shared interfaces

Neither workstream changes these while parallel development is active:

- The mecanum equations and positive-axis convention in IMPLEMENTATION_PLAN.md.
- The serial drive packet: type, session, seq, forward, sideways, turn,
  and speed_limit.
- The acknowledgment: session, ack, fl, fr, rl, and rr.
- Arm/disarm semantics, 250 ms Pi-side deadlines, and 500 ms firmware watchdog.
- Generic detection-event structure and normalized bounding-box coordinates.

An interface change requires a short decision in changes.md, synchronized
updates to both branches, and an updated parity fixture before either branch
continues implementation.

## Merge gates

1. Dashboard/control can complete browser-to-mock driving through Stage D
   without firmware availability.
2. ESP32-controller can complete parser, mixing, watchdog, and IMU work through
   Stage F against the frozen protocol and fixtures.
3. The integrator merges the dashboard/control branch first after its automated
   tests pass, then merges the firmware branch after its tests compile/pass.
4. Perform Stage G only from the integrated main branch with validated hardware
   configuration and the chassis raised for initial tests.
5. Begin camera work only after basic real driving is stable. Camera work cannot
   block the motor-control loop or delay Stop.
6. Do not start Stage L or deferred features until the physical acceptance list
   in IMPLEMENTATION_PLAN.md passes.

## Handoffs and integration

During concurrent work:

- Dashboard/control updates only docs/handoffs/dashboard-control.md.
- ESP32-controller updates only docs/handoffs/esp32-controller.md.
- The integrator updates changes.md when a branch merges.
- Do not modify README.md, AGENTS.md, IMPLEMENTATION_PLAN.md, changes.md, or
  the other workstream's handoff file from a feature branch.
- Put branch-specific run, flash, and test instructions in its handoff file;
  move verified shared instructions to README.md during integration.

Each handoff entry records commit, changed interfaces, exact test commands,
mock versus physical coverage, known limitations, and the next action.
Use small commits. Preserve passing commits and do not rewrite shared history.
