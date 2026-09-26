# ESP32 controller handoff

Workstream: ESP32 controller
Branch/worktree: claude/esp32-user-setup-f3c69c (dedicated worktree; see
"Branch naming discrepancy" below)
Status: in progress, uncommitted

This workstream owns the files listed in WORKSTREAMS.md.
Do not edit shared project documents while parallel work is active.

## Branch naming discrepancy

WORKSTREAMS.md names this workstream's branch `feature/esp32-controller`,
branched from tag `stage-0-docs`. This session's assigned worktree is
`claude/esp32-user-setup-f3c69c` instead, and no `stage-0-docs` tag exists
in this repository yet (history stops at 70bc2cc, "docs: define parallel
workstreams", one commit past what changes.md calls the Stage 0 checkpoint).
The worktree is still dedicated to this workstream alone (AGENTS.md's "one
task branch and separate worktree per concurrent workstream" requirement is
met), so work proceeded here rather than blocking on a rename. Flagging this
so the integrator can rename/re-base onto `feature/esp32-controller` from
the correct checkpoint if that matters for the merge, and can create the
`stage-0-docs` tag retroactively if it's still wanted as an immutable
reference point.

## Current state

- Base commit: 70bc2cc ("docs: define parallel workstreams").
- Created the Stage E/F firmware skeleton in `firmware/` around the frozen
  serial protocol: pure mecanum mixing, wheel wiring/inversion
  configuration, arm/disarm/session/sequence/watchdog state machine, a
  bounded nonblocking line reader, a JSON packet codec, and Adafruit
  Motor Shield V2 / BNO055 hardware adapters wired together in `main.cpp`.
- Native (host, no board) Unity test suites exist for the pure-logic
  modules: mixing, session/watchdog, and protocol parsing/building.
- **Not compiled or run.** No C++ toolchain (no PlatformIO, no g++/clang++)
  was available in the sandbox that wrote this code. See "Tests" below for
  exactly what was and was not verified.
- Hardware adapters (`motor_shield.cpp`, `imu_bno055.cpp`) and `main.cpp`
  are guarded with `#ifdef ARDUINO` so native test builds compile them to
  empty translation units; they still need a real board to validate at all.

## Files added

- `firmware/platformio.ini` — `esp32-s2` (placeholder board
  `esp32-s2-saola-1`, update once confirmed) and `native` (host test) envs.
  `test_dir` is set to `tests` to match WORKSTREAMS.md's stated path
  (`firmware/tests/`) rather than PlatformIO's default `test/`.
- `firmware/include/`, `firmware/src/`: `mixing.{h,cpp}` (pure calculation,
  zero dependencies), `chassis_config.h` (wheel mapping/inversion +
  `hardware_pwm_ceiling`, defaulted to 0 — see below), `wiring.h` (applies
  chassis config to a mixing result), `session_guard.{h,cpp}` (arm/disarm/
  session/sequence/watchdog, zero dependencies), `line_reader.{h,cpp}`
  (bounded nonblocking byte-to-line accumulation, zero dependencies),
  `protocol_messages.h` + `protocol_codec.cpp` (JSON in/out, uses
  ArduinoJson v6), `motor_shield.{h,cpp}`, `imu_bno055.{h,cpp}`, `main.cpp`.
- `firmware/tests/test_mixing/`, `test_session_guard/`, `test_protocol_codec/`
  — Unity native test suites.
- `firmware/tests/fixtures/mixing_fixtures.json` +
  `generate_mixing_fixtures.py` — the shared mecanum mixing behavioral
  fixtures (14 cases: logical-direction tests, normalization/saturation
  boundaries, zero-limit, full-PWM-ceiling). Values were computed and
  cross-checked in Python (executable in this sandbox) and the generator's
  output was diffed against the committed file to confirm they match.
- `.gitignore` (repo root, new) — ignores `firmware/.pio/` only, scoped to
  this workstream's build output.

## Interface decisions needing dashboard/control sync

The drive packet and its ack (session/ack/fl/fr/rl/rr) match the frozen
shape in IMPLEMENTATION_PLAN.md exactly. The wire format for **arm/disarm**
was not fully specified there (only the semantics are frozen), so this
workstream defined one and used it in `protocol_messages.h`/`main.cpp`:

- Inbound: `{"type":"arm","session":"...","seq":N}`,
  `{"type":"disarm","session":"...","seq":N}`.
- Outbound: `{"type":"arm_ack","session":"...","seq":N,"armed":true}`,
  `{"type":"disarm_ack","session":"...","seq":N,"armed":false}`,
  `{"type":"fault","reason":"...","armed":false}` (sent on any malformed
  packet or watchdog expiry), `{"type":"imu","timestamp_ms":N,
  "available":bool,"heading":deg,"calibration":0-3}` at ~20 Hz.

Malformed-packet policy implemented: any line that fails to parse as the
expected flat JSON shape, has a wrong-typed/missing/out-of-range field, or
exceeds the bounded line length is treated as malformed — motors are cut
and the guard disarms immediately. A well-formed but stale/duplicate/
out-of-order/wrong-session **drive** packet is silently ignored instead
(no motor change, no watchdog refresh, no disarm), per
IMPLEMENTATION_PLAN.md section 6.

Before Stage G integration, please reconcile these with whatever the
dashboard/control workstream's serial motor bridge expects, and record any
agreed change in changes.md per WORKSTREAMS.md's frozen-interface process.

## Hardware facts still required

Unchanged from changes.md "Open hardware facts" — still blocking real
driving, not mock/native development:

- Exact ESP32-S2 board and I2C pins (`platformio.ini` uses a placeholder
  board id).
- Confirmed motor-shield revision/address.
- Wheel-channel mapping and direction inversions (`chassis_config.h` has
  placeholder, unvalidated defaults).
- Validated motor-output ceiling — `ChassisConfig::hardware_pwm_ceiling`
  defaults to **0**, so the firmware cannot command any real motor output
  until this is set from physical validation
  (IMPLEMENTATION_PLAN.md section 7).
- BNO055 mounting/calibration details.

## Tests

- **Not run: any C++ compilation or execution.** The sandbox that wrote
  this code had no PlatformIO, no `arduino-cli`, and no `g++`/`clang++`/
  `cmake` available, so none of the C++ (native test suites or the
  esp32-s2 build) has been compiled even once. Do not treat any C++ file
  here as verified until `pio test -e native` actually passes.
- Verified: the mixing fixture values themselves. `python
  firmware/tests/fixtures/generate_mixing_fixtures.py` was run in this
  sandbox; its output was byte-for-byte diffed against the committed
  `mixing_fixtures.json`, confirming the arithmetic (normalization,
  scaling, rounding, clamping) is self-consistent with a from-scratch
  Python reimplementation of the same steps. This checks the *fixture
  numbers*, not that `mixing.cpp` compiles or produces them.
- Not performed: any physical/hardware test.

## Known limitations

- `protocol_codec.cpp` is written against the ArduinoJson v6.21 API from
  memory and is the highest-risk file here for an API mismatch; verify it
  first.
- `SessionGuard`/`protocol_codec` sequence numbers are validated as
  `long`/`long long` via `is<int>()`; very large `seq` values (beyond
  int32 range) are untested and may need widening once checked against
  real ArduinoJson integer-type behavior.
- `motor_shield.cpp` / `imu_bno055.cpp` are written against the Adafruit
  Motor Shield V2 / Adafruit BNO055 library APIs from memory; unverified
  against real headers or hardware.
- No firmware compilation, flashing, or physical test has occurred.

## Next action

1. Install PlatformIO; run `pio test -e native` from `firmware/` and fix
   any compile errors (expect the ArduinoJson usage to need the closest
   look).
2. Once native tests pass, attempt `pio run -e esp32-s2` to check the
   Arduino-side code compiles against the real libraries; update
   `platformio.ini`'s `board` once the exact ESP32-S2 board is confirmed.
3. Reconcile the arm/disarm/fault/imu wire formats above with
   dashboard/control's serial motor bridge before Stage G.
4. Resolve the "Hardware facts still required" list, then set
   `chassis_config.h` from validated values before any real motor output.

## Handoff log

### 2026-09-26 EDT - Stage E/F firmware skeleton

- Commit: uncommitted at time of writing.
- Changed files and interfaces: see "Files added" and "Interface decisions
  needing dashboard/control sync" above.
- Tests and results: see "Tests" above — native C++ suites written but
  never compiled/run in this environment; only the mixing fixture
  arithmetic was independently verified via Python.
- Mock or physical coverage: neither; this is uncompiled firmware source.
- Known limitations: see "Known limitations" above.
- Next action: see "Next action" above.

## Entry template

### <ISO date/time with timezone> - <short task>

- Commit:
- Changed files and interfaces:
- Tests and results:
- Mock or physical coverage:
- Known limitations:
- Next action:
