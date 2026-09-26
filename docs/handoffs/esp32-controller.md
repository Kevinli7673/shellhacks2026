# ESP32 controller handoff

Workstream: ESP32 controller
Branch: feature/esp32-controller (pushed to origin)
Status: in progress, committed locally

This workstream owns the files listed in WORKSTREAMS.md.
Do not edit shared project documents while parallel work is active.

## Current state

- Base commit: 70bc2cc ("docs: define parallel workstreams"); first
  skeleton committed as 48ae9dd on this branch.
- Stage E/F firmware skeleton in `firmware/` around the frozen serial
  protocol: pure mecanum mixing, wheel wiring/inversion configuration,
  arm/disarm/session/sequence/watchdog state machine (now factored into a
  hardware-agnostic `Controller`, see below), a bounded nonblocking line
  reader, a JSON packet codec, and Adafruit Motor Shield V2 / BNO055
  hardware adapters wired together in `main.cpp`.
- Applied the review feedback on 48ae9dd from dashboard/control
  (feature/serial-protocol): stale-arm rejection, a boot fault message, and
  zeroing motors on arm. See "Changes since 48ae9dd" below for exactly what
  changed and why.
- Native (host, no board) Unity test suites exist for the pure-logic
  modules — mixing, session/watchdog, protocol parsing/building — plus a
  new suite that replays the shared `fixtures/serial_protocol_vectors.json`
  (26 cases) against the firmware's actual logic via the new `Controller`
  class.
- **Still not compiled or run.** No C++ toolchain (no PlatformIO, no
  g++/clang++) is available in this sandbox. See "Tests" below for exactly
  what was and was not verified.
- Hardware adapters (`motor_shield.cpp`, `imu_bno055.cpp`) and `main.cpp`
  are guarded with `#ifdef ARDUINO` so native test builds compile them to
  empty translation units; they still need a real board to validate at all.
- The `firmware/tests/` → `firmware/test/` directory rename (matching
  PlatformIO's default, done outside this session) is reflected everywhere
  in code/docs now. WORKSTREAMS.md still says `firmware/tests/` — flagging
  the discrepancy per AGENTS.md rather than editing that shared doc myself;
  someone should update it or confirm the rename is intentional.

## Files added

- `firmware/platformio.ini` — `esp32-s2` (placeholder board
  `esp32-s2-saola-1`, update once confirmed) and `native` (host test) envs.
  `test_dir` is left at PlatformIO's default (`test/`), matching the
  rename above.
- `firmware/include/`, `firmware/src/`: `mixing.{h,cpp}` (pure calculation,
  zero dependencies), `chassis_config.h` (wheel mapping/inversion +
  `hardware_pwm_ceiling`, defaulted to 0 — see below), `wiring.h` (applies
  chassis config to a mixing result), `session_guard.{h,cpp}` (arm/disarm/
  session/sequence/watchdog, zero dependencies), `controller.h`/`.cpp`
  (new — orchestrates a processed line against SessionGuard + mixing;
  shared verbatim between `main.cpp` and the fixture-replay test so the
  same logic is what's actually tested), `line_reader.{h,cpp}` (bounded
  nonblocking byte-to-line accumulation, zero dependencies),
  `protocol_messages.h` + `protocol_codec.cpp` (JSON in/out, uses
  ArduinoJson v6), `motor_shield.{h,cpp}`, `imu_bno055.{h,cpp}`, `main.cpp`
  (now just Serial I/O plumbing around `Controller`).
- `firmware/test/test_mixing/`, `test_session_guard/`, `test_protocol_codec/`,
  `test_protocol_fixtures/` (new) — Unity native test suites.
- `firmware/test/fixtures/mixing_fixtures.json` +
  `generate_mixing_fixtures.py` — the shared mecanum mixing behavioral
  fixtures (14 cases: logical-direction tests, normalization/saturation
  boundaries, zero-limit, full-PWM-ceiling). Values were computed and
  cross-checked in Python (executable in this sandbox) and the generator's
  output was diffed against the committed file to confirm they match.
- `firmware/test/fixtures/serial_protocol_vectors.json` (new) — vendored
  verbatim from `fixtures/serial_protocol_vectors.json` @ b23d95e on
  `feature/serial-protocol` (26 cases), so `test_protocol_fixtures` is
  self-contained rather than depending on cross-branch relative paths that
  can't be verified without running PlatformIO. This is a working copy for
  testing, not a fork of ownership — `fixtures/` at repo root stays
  dashboard/control's path; the integrator should dedupe these at merge.
- `.gitignore` (repo root) — ignores `firmware/.pio/` and `.idea/`, scoped
  to this workstream's build output and local IDE state.

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

### Extra/duplicate JSON field policy

Requested a decision on this; here it is:

- **Extra unknown fields: ignored**, not rejected. `parseInbound` only ever
  reads the specific keys it expects for the message's `type`; anything
  else in the object is never looked at. Chosen for forward-compatibility
  (a field either side adds later doesn't require both sides to deploy
  atomically). If you'd rather reject them for stricter unwanted-field
  detection, that's a small change on this end — just say so.
- **Duplicate keys**: not explicitly validated on this end at all; this
  falls through to whatever ArduinoJson v6's `deserializeJson` does with a
  repeated key, which I believe (unverified — no compiler here) is
  first-occurrence-wins on lookup (`doc["seq"]` returns the first `"seq"`
  entry parsed, with later duplicates parsed but never read). I'm not
  planning to add explicit duplicate-key rejection on top of that unless
  you'd rather have it — a legitimate sender producing duplicate keys is a
  sender bug to catch on your side, not something this parser needs to
  defend against as an attacker input.

## Changes since 48ae9dd

Applied all three requested behavior changes:

1. **Stale arms ignored.** `SessionGuard::arm()` now returns `bool` and
   rejects (no state change at all) when the incoming session matches the
   currently remembered session and `seq` is not higher than the last one
   seen for it. An arm for any other session is always accepted — a
   genuine reconnect starts a fresh session, so this can never block one.
   `main.cpp`/`Controller::handleLine` send an `arm_ack` only when `arm()`
   returns true; a rejected stale arm gets no ack, matching how a stale
   drive packet is handled.
2. **Boot message.** `Controller::boot()` sends
   `{"type":"fault","reason":"boot","armed":false}` once, called from
   `setup()` right after motors are forced off (still before sensor init,
   per section 7).
3. **Motors zeroed on arm.** Any *accepted* arm now zeroes
   `Controller`'s tracked outputs before sending the ack, so a re-arm can
   never leave stale motor output in place; the first drive packet after
   arming computes fresh outputs as before. A stale/rejected arm leaves
   outputs untouched, same as it leaves everything else untouched.

Also refactored: the protocol/safety decision logic that used to live
directly in `main.cpp` (parse → validate → mix → ack) is now in a new
`Controller` class (`controller.h`/`.cpp`), hardware-agnostic and shared
verbatim between `main.cpp` and the new `test_protocol_fixtures` native
test. This was to make "run our shared protocol fixtures" possible at all
without either duplicating that logic into the test (risking drift) or
needing a real board.

**Known stale expectation in the shared fixture:** applying change 3 makes
one already-committed case in `fixtures/serial_protocol_vectors.json`,
`rearm_with_new_session_rejects_old_session`, expect wrong `outputs` for
two of its steps — it still expects `100/100/100/100` to persist through
the arm to session `pi02`, since it was written against pre-fix behavior.
Post-fix, those steps' `outputs` should be `0/0/0/0` instead (everything
else in that case — the acks, `armed`, the final drive's outputs — still
matches). Flagging rather than editing, since `fixtures/` is your path;
`test_protocol_fixtures` in this branch is expected to report exactly this
one mismatch once it actually compiles and runs.

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
  esp32-s2 build) has been compiled even once, including the new
  `Controller` refactor and `test_protocol_fixtures`. Do not treat any C++
  file here as verified until `pio test -e native` actually passes.
- Verified: the mixing fixture values themselves (unchanged from before —
  `python firmware/test/fixtures/generate_mixing_fixtures.py`, diffed
  byte-for-byte against the committed file after the directory rename).
  Also traced `test_protocol_fixtures`'s expected behavior by hand against
  all 26 vendored cases (watchdog boundary timing at 500ms, stale/wrong-
  session/duplicate-seq rejection, hardware-ceiling override, oversized-
  line handling) — all should pass except the one known mismatch noted
  above. This is manual trace verification, not execution.
- Not performed: any physical/hardware test.

## Known limitations

- `protocol_codec.cpp` and `test_protocol_fixtures.cpp` are written against
  the ArduinoJson v6.21 API from memory and are the highest-risk files here
  for an API mismatch (object/array iteration, `containsKey`, the
  `variant | default` idiom); verify these first.
- **Fixed, previously a real bug:** `protocol_codec.cpp`'s
  `StaticJsonDocument` capacity was 256 bytes, too small to parse a full
  drive packet — `deserializeJson` would return `NoMemory` and every valid
  drive packet would be treated as malformed (fault + disarm). Raised to
  512; see the 2026-09-26 "Verified via isolated PlatformIO install" log
  entry below for how this was found.
- `SessionGuard`/`protocol_codec` sequence numbers are validated as
  `long`/`long long` via `is<int>()`; very large `seq` values (beyond
  int32 range) are untested and may need widening once checked against
  real ArduinoJson integer-type behavior.
- `motor_shield.cpp` / `imu_bno055.cpp` are written against the Adafruit
  Motor Shield V2 / Adafruit BNO055 library APIs from memory; unverified
  against real headers or hardware.
- `test_protocol_fixtures.cpp` tries several candidate relative paths to
  find the vendored fixture file since PlatformIO's native test working
  directory isn't verified in this sandbox; set `RESCUEBOT_FIXTURE_DIR` if
  none of them hit.
- `platformio.ini`'s `[env:native]` was missing `test_build_src = yes` and
  `-D UNITY_INCLUDE_DOUBLE`; both added (see log below). Without the
  former, every native suite fails to link; without the latter,
  double-precision assertions silently no-op instead of comparing.
- No firmware compilation, flashing, or physical test has occurred. This
  machine specifically has no C/C++ compiler at all (verified directly —
  no gcc/g++/clang/clang++/cl anywhere), so `pio test -e native` cannot be
  compiled here regardless of source correctness; see the log entry below.

## Next action

1. Install PlatformIO; run `pio test -e native` from `firmware/` and fix
   any compile errors (expect the ArduinoJson usage, especially in
   `test_protocol_fixtures.cpp`, to need the closest look).
2. Confirm `test_protocol_fixtures` reports exactly the one known mismatch
   in `rearm_with_new_session_rejects_old_session` and nothing else; update
   that case's expected `outputs` on the `feature/serial-protocol` side.
3. Once native tests pass, attempt `pio run -e esp32-s2` to check the
   Arduino-side code compiles against the real libraries; update
   `platformio.ini`'s `board` once the exact ESP32-S2 board is confirmed.
4. Let us know if you'd rather extra/duplicate JSON fields be handled
   differently than described above.
5. Resolve the "Hardware facts still required" list, then set
   `chassis_config.h` from validated values before any real motor output.
6. Someone should reconcile WORKSTREAMS.md's `firmware/tests/` path with
   the actual `firmware/test/` directory name.

## Handoff log

### 2026-09-26 EDT - Stage E/F firmware skeleton

- Commit: 48ae9dd on feature/esp32-controller (pushed to origin).
- Changed files and interfaces: see "Files added" and "Interface decisions
  needing dashboard/control sync" above.
- Tests and results: native C++ suites written but never compiled/run in
  this environment; only the mixing fixture arithmetic was independently
  verified via Python.
- Mock or physical coverage: neither; this was uncompiled firmware source.
- Known limitations: ArduinoJson v6 usage in `protocol_codec.cpp`
  unverified; hardware adapters unverified against real libraries/hardware.
- Next action: install PlatformIO and run `pio test -e native`.

### 2026-09-26 EDT - Review feedback: stale arms, boot message, zero-on-arm, shared fixtures

- Commit: uncommitted at time of writing (on feature/esp32-controller).
- Changed files and interfaces: `session_guard.h`/`.cpp` (`arm()` now
  returns `bool`, rejects stale/replayed arms), new `controller.h`/`.cpp`
  (protocol/safety orchestration factored out of `main.cpp`), `main.cpp`
  (now Serial I/O plumbing around `Controller`; sends the boot fault
  message), new `test_protocol_fixtures` suite plus vendored
  `serial_protocol_vectors.json`. See "Changes since 48ae9dd" above for the
  full rationale.
- Tests and results: still no compiler available; changes were traced by
  hand against all 26 vendored fixture cases (see "Tests" above). Native
  `pio test -e native` still needs to actually run this.
- Mock or physical coverage: neither.
- Known limitations: see "Known limitations" above — `test_protocol_fixtures.cpp`
  is the least-verified file added so far.
- Uncommitted work: this entire entry's changes are uncommitted as written.
- Coordination or merge notes: one known stale expectation in
  `fixtures/serial_protocol_vectors.json` needs an update on the
  dashboard/control side (see "Changes since 48ae9dd"); WORKSTREAMS.md's
  `firmware/tests/` path is now stale.
- Next action: see "Next action" above.

### 2026-09-26 EDT - Verified via isolated PlatformIO install; two real bugs fixed; one claimed fixture update does not exist

A second-hand report (relayed as "the dashboard/control workstream's
observations from running `pio test -e native` on macOS") was checked
against this branch before anything was changed, per its own instruction to
"reproduce every finding yourself." Three of its four code-level claims
reproduced as real, independently-diagnosable bugs and are fixed below. Its
fixture-file claim does not hold up — recorded as a discrepancy, not acted
on.

**Setup (isolated, off the system, original branch/worktree untouched):**

```
python -m venv <scratch>/venv          # first attempt failed: pip's own
                                        # install path exceeded Windows
                                        # MAX_PATH under the assigned deep
                                        # scratchpad directory; recreated
                                        # under a short C:\...\Temp\rbpio_*
                                        # path instead
<venv>/Scripts/python.exe -m pip install platformio   # -> platformio 6.2.0
git archive HEAD | tar -x -C <scratch>/export          # unmodified 3cbea5f
PLATFORMIO_CORE_DIR=<scratch>/core <venv>/Scripts/pio.exe test -e native
```

**Before any changes** (unmodified 3cbea5f): PlatformIO installed cleanly
and downloaded its `native` platform, `tool-scons`, `ArduinoJson@6.21.6`,
and `Unity@2.6.1` without issue — so PlatformIO itself, and the declared
library versions, are fine. All 4 suites then **ERRORED at the compile
step**: `'gcc' is not recognized as an internal or external command` /
`'g++' is not recognized...` for every `.o`. This machine has no `gcc`,
`g++`, `clang`, `clang++`, or `cl` anywhere on `PATH` or in any common
install location (checked directly before starting), and PlatformIO's
`native` platform expects one already installed — it doesn't bundle a host
compiler the way its embedded platforms bundle a cross toolchain. This is a
different, earlier failure than the reported one (which was a *link*
failure with undefined symbols, meaning compilation itself succeeded on
their machine).

**Reproduced by static analysis + code inspection (could not compile to
confirm the before/after difference directly, for the reason above):**

1. `test_build_src = yes` missing from `[env:native]` — confirmed
   plausible and applied. Rerunning after the fix visibly changed
   PlatformIO's behavior: it now attempts `src/controller.o`,
   `src/imu_bno055.o`, `src/line_reader.o` as link inputs, where before the
   fix none of `src/` was touched at all — this is exactly the mechanism
   that would produce "undefined symbol" errors without it. Could not
   observe an actual link succeed either way, since compilation never gets
   that far here.
2. `-D UNITY_INCLUDE_DOUBLE` missing — applied. Correct per Unity's own
   documented default (double-precision assertions disabled unless this is
   defined); `test_protocol_codec.cpp` uses `TEST_ASSERT_EQUAL_DOUBLE` in
   `test_valid_drive_packet_parses` and the mixing/session tests use
   floating axis values, so this tracks. Not independently re-run past
   compilation for the same reason as above.
3. `kJsonCapacity = 256` in `protocol_codec.cpp` too small — **agreed,
   this is a real bug independent of any test result.** A `StaticJsonDocument`
   sized for the parsed representation of a 7-field drive packet
   (type/session/seq/forward/sideways/turn/speed_limit) plus a duplicated
   session string is plausibly over 256 bytes once ArduinoJson v6's
   per-field node overhead is counted, especially on a 64-bit host where
   that overhead is larger than on the 32-bit ESP32-S2 target sharing the
   same constant. If real, this means **every valid drive packet would
   have made the real firmware fault and disarm** — the robot could never
   actually drive. Raised to 512. Kept `LineReader`'s independent 200-byte
   line-length bound (`kMaxLineLength` in `line_reader.h`) unchanged, so
   the wire-level bound and the parser's internal memory budget stay two
   separate concerns, per the request.
4. `StaticJsonDocument<128>` in `test_protocol_codec.cpp`'s
   `test_build_imu_telemetry_round_trips` / `_unavailable_omits_heading` —
   same root cause, in the test's own round-trip re-parse rather than the
   firmware. Raised both to 256.

**Not done — reported instead of acted on:** checked every branch on
`origin` (`git fetch --prune`, then compared `fixtures/serial_protocol_vectors.json`
across all of them) for the claimed "updated 30-case" version with new
cases for stale-arm-ignored / zero-on-arm / arm-after-disarm / extra-fields
/ a top-level `boot_emit`. **It does not exist anywhere on origin.**
`origin/feature/dashboard-control` at `3393fa4` has the file, and it is
byte-for-byte identical (`diff` shows no output) to the 26-case file
already vendored in this branch — the same content, not an update. Did not
fabricate a 30-case file or edit the vendored one to compensate. If a
30-case version exists, it hasn't been pushed anywhere I can reach; please
push it, or point at the right branch/commit, and I'll redo this step.

**Comparison to the reported 52/52:** cannot compare pass/fail counts at
all — this environment cannot compile a single native test file, before or
after any of these fixes, for the reasons above (no host C/C++ compiler
exists on this machine). The fixes above are applied because they're
independently correct on inspection, not because they were observed to
turn failures into passes here.

**esp32-s2 build:** not attempted. `pio run -e esp32-s2` would use
PlatformIO's own bundled Xtensa toolchain (no system compiler needed for
that env, unlike `native`) and was going to be tried build-only, no
flashing — declined before it ran, so it stayed untried. Still open as a
way to get real compiler feedback on `protocol_codec.cpp` and the other
previously "written from memory" files without needing any hardware.

**Remains completely unverified:** the esp32-s2 board build, flashing, and
anything physical/hardware. Also still open: the one already-flagged stale
expectation in `rearm_with_new_session_rejects_old_session` (this entry
found no newer file that would have superseded that flag), and whether a
real `pio test -e native` run (on a machine with an actual compiler) turns
up anything beyond these four fixes.

- Commit: pending (about to commit `platformio.ini`, `protocol_codec.cpp`,
  `test_protocol_codec.cpp`).
- Changed files and interfaces: `firmware/platformio.ini` (`[env:native]`:
  `test_build_src = yes`, `+ -D UNITY_INCLUDE_DOUBLE`),
  `firmware/src/protocol_codec.cpp` (`kJsonCapacity` 256 → 512),
  `firmware/test/test_protocol_codec/test_protocol_codec.cpp`
  (`StaticJsonDocument<128>` → `<256>`, two call sites). No message shapes,
  mixing equations, or `hardware_pwm_ceiling` touched.
- Tests and results: see above — PlatformIO itself verified working via an
  isolated venv; native compilation still blocked on this machine by a
  missing host compiler, before and after these fixes.
- Mock or physical coverage: neither.
- Known limitations: same as before, plus — these four fixes are applied
  on the strength of code inspection and the partial `test_build_src`
  behavioral confirmation, not a passing test run.
- Coordination or merge notes: the claimed 30-case
  `fixtures/serial_protocol_vectors.json` update was not found on any
  origin branch (see above) — please push it or correct the pointer.
- Next action: get this running somewhere with an actual C/C++ toolchain
  (or retry the `esp32-s2` build-only path) to turn "should pass" into an
  observed result; locate/push the real 30-case fixture file.

## Entry template

### <ISO date/time with timezone> - <short task>

- Commit:
- Changed files and interfaces:
- Tests and results:
- Mock or physical coverage:
- Known limitations:
- Next action:
