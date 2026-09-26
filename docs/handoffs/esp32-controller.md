# ESP32 controller handoff

Workstream: ESP32 controller
Branch: feature/esp32-controller (pushed to origin)
Status: in progress; native tests 52/52; esp32-s2 build succeeds; not flashed, no hardware test

This workstream owns the files listed in WORKSTREAMS.md.
Do not edit shared project documents while parallel work is active.

## Requests

Requests from ESP32-controller to the dashboard/control workstream. See
AGENTS.md "Cross-workstream requests".

| ID | To | Request | Status |
|---|---|---|---|

## Responses

Responses from ESP32-controller to dashboard/control requests (DC-#).

| Request | Response |
|---|---|
| DC-1 | Done. Merged origin/main (b37213e) into feature/esp32-controller as 1988d5a; this Requests/Responses section was added in the commit that introduced it. |
| DC-2 | Done in f6213f3. `firmware/test/fixtures/serial_protocol_vectors.json` is byte-identical to origin/feature/dashboard-control (checked against e101f82 and again at 585d499). `pio test -e native` on 0332bd9 plus that file: 52/52 pass on Windows 11, GCC 15.2.0 (MinGW-w64), PlatformIO 6.2.0, ArduinoJson 6.21.6. Details in the handoff log below. |
| DC-3 | Done. `pio run -e esp32-s2` (build only, nothing flashed) on 1988d5a: SUCCESS, 0 compiler warnings, RAM 4.8% (15620/327680 B), flash 20.6% (269566/1310720 B). Board is still the placeholder esp32-s2-saola-1. Toolchain came from PlatformIO's registry into an isolated core dir. |

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
  suite that replays the shared `fixtures/serial_protocol_vectors.json`
  (30 cases + `boot_emit`) against the firmware's actual logic via
  `Controller`.
- **Native tests compiled and run: 52/52 pass** (PlatformIO 6.2.0, GCC
  15.2.0 on Windows). `pio run -e esp32-s2` also builds (0 warnings). No
  flashing or hardware testing has happened. See "Tests" below.
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
- `firmware/test/fixtures/serial_protocol_vectors.json` — vendored
  verbatim (byte-identical) from `fixtures/serial_protocol_vectors.json` @
  e101f82 on `feature/dashboard-control` (30 cases + top-level
  `boot_emit`), so `test_protocol_fixtures` is self-contained. This is a
  working copy for testing, not a fork of ownership — `fixtures/` at repo
  root stays dashboard/control's path; do not edit the cases here, and the
  integrator should dedupe these at merge.
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
- **Duplicate keys**: not explicitly rejected. Measured with ArduinoJson
  6.21.6: the **last** occurrence wins — `{"seq":3,"seq":9,...,"forward":1.0,
  ...,"forward":-1.0}` parses as not malformed with `seq=9`,
  `forward=-1.0`. (An earlier version of this doc guessed first-occurrence;
  that was wrong.) The duplicated value still goes through the same range
  and type validation, so this can't bypass the malformed-packet checks.
  No legitimate sender produces duplicate keys; if you want them rejected
  outright, that needs explicit detection on this end.

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

The shared fixture's `rearm_with_new_session_rejects_old_session` case
originally expected pre-fix outputs (100/100/100/100 kept through the
re-arm). The 30-case version at e101f82 updates it to 0/0/0/0 and adds
cases for the other changes; all pass against this firmware.

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

Run from `firmware/` (PlatformIO needs a host C/C++ compiler on `PATH` for
the `native` env; it does not bundle one):

    pio test -e native

- Native: **52/52 pass** — test_mixing 15, test_protocol_codec 21,
  test_session_guard 15, test_protocol_fixtures 1 (replays all 30 shared
  cases plus `boot_emit`). Environment: Windows 11, PlatformIO Core 6.2.0,
  GCC 15.2.0 (MinGW-w64), ArduinoJson 6.21.6, Unity 2.6.1. This matches the
  dashboard/control cross-check (macOS arm64, Apple clang).
- Mixing fixture values are also reproducible from
  `python firmware/test/fixtures/generate_mixing_fixtures.py`.
- Board build: `pio run -e esp32-s2` → SUCCESS, 0 warnings, RAM 4.8%,
  flash 20.6% (espressif32 platform, Arduino framework, Adafruit Motor
  Shield V2 1.1.4, Adafruit BNO055 1.6.4, ArduinoJson 6.21.6).
- Not performed: flashing, or any physical/hardware test.

## Known limitations

- **Fixed, previously a real bug:** `protocol_codec.cpp`'s
  `StaticJsonDocument` capacity was 256 bytes. A typical drive packet needs
  288 bytes on a 64-bit host, so `deserializeJson` returned `NoMemory` and
  every valid drive packet was treated as malformed (fault + disarm) —
  3cbea5f could never have driven. Raised to 512 in 0332bd9.
- Parser headroom at 512 (measured on the 64-bit host): a conformant drive
  packet with a maximum-length session uses 321 bytes; a 195-byte line
  padded with unknown extra fields uses 496. A pathological ≤200-byte line
  packed with more tiny extra fields could still exceed 512; that fails
  safe (`NoMemory` → malformed → fault + disarm), and no conformant sender
  produces it. The ESP32-S2 (32-bit) needs less memory per field than the
  host, so this is the conservative case.
- `SessionGuard`/`protocol_codec` sequence numbers are validated via
  `is<int>()`; `seq` values beyond int32 range are untested.
- `motor_shield.cpp` / `imu_bno055.cpp` / `main.cpp` (behind
  `#ifdef ARDUINO`) compile against the real Arduino/Adafruit headers but
  have never run on hardware.
- `test_protocol_fixtures.cpp` tries several candidate relative paths to
  find the vendored fixture file; set `RESCUEBOT_FIXTURE_DIR` if none hit.
  Its failure messages are sometimes truncated in PlatformIO's summary
  (text after a `:` is dropped); use `pio test -v` to see full text.
- No board build, flashing, or physical test has occurred.

## Next action

1. Confirm the exact ESP32-S2 board and update `platformio.ini`'s `board`,
   then rebuild.
2. Resolve the "Hardware facts still required" list, then set
   `chassis_config.h` from validated values before any real motor output.
3. Someone should reconcile WORKSTREAMS.md's `firmware/tests/` path with
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

### 2026-09-26 EDT - Native tests compiled and run: all four observations reproduced, 52/52 pass

Supersedes the "could not compile" parts of the previous entry. The fixes
from that entry were committed as 0332bd9. A host compiler is now
available (GCC 15.2.0 from the MinGW-w64 bundled with CLion, prepended to
`PATH` for the test process only — no system PATH or git config change).

Environment: Windows 11, PlatformIO Core 6.2.0 in an isolated venv with
`PLATFORMIO_CORE_DIR` pointed at a scratch directory, ArduinoJson 6.21.6,
Unity 2.6.1. Every run used a `git archive` export in scratch, not the
worktree.

Commands (from the exported `firmware/`):

    PATH="<clion>/bin/mingw/bin:$PATH" PLATFORMIO_CORE_DIR=<scratch>/core \
      <venv>/Scripts/pio.exe test -e native

Results, isolating each observation on the unmodified 3cbea5f:

| Build | Result |
|---|---|
| 3cbea5f as pushed | All 4 suites fail to link: `undefined reference to rescuebot::mix`, `SessionGuard::*`, `parseInbound`, `Controller::*`, `LineReader::*` (observation 1 reproduced) |
| 3cbea5f + `test_build_src = yes` only | 48 pass, 4 fail: `test_valid_drive_packet_parses`, `test_drive_packet_tolerates_unknown_extra_fields` (parsed as malformed), `test_build_imu_telemetry_round_trips`, and the fixture replay with 91 mismatches — every valid drive packet produced `malformed_packet` + disarm (observations 3 and 4 reproduced) |
| 3cbea5f + src + capacity fixes, no `UNITY_INCLUDE_DOUBLE` | 49 pass, 3 fail: two with `Unity Double Precision Disabled` (observation 2 reproduced; it is masked until 3/4 are fixed), plus the 2 known `rearm_with_new_session_rejects_old_session` mismatches |
| 0332bd9 (all four fixes), 26-case vectors | 51 pass, 1 fail: only the 2 `rearm_with_new_session_rejects_old_session` mismatches predicted earlier (t=60, t=70 outputs) |
| 0332bd9 + 30-case vectors + `boot_emit` check (this entry) | **52/52 pass** |

`DeserializationError` confirmed with a scratch-only diagnostic (not
committed): capacity 256 → `NoMemory` (memoryUsage 256, needs 288); 384 and
512 → `Ok`. Kept 512 (matches the cross-check; headroom figures are under
"Known limitations").

Comparison with dashboard/control's 52/52 (macOS arm64, Apple clang): same
count, same result. No differences.

Changes in this entry:

- `firmware/test/fixtures/serial_protocol_vectors.json`: replaced with the
  30-case version from e101f82 on `feature/dashboard-control`,
  byte-identical (verified with `diff`), cases not edited. This file was
  not on origin when the previous entry was written; it was pushed later
  as e101f82, which is why that entry reported it missing.
- `firmware/test/test_protocol_fixtures/test_protocol_fixtures.cpp`: calls
  `Controller::boot()` at the start of each case (mirrors `setup()`), and
  checks the top-level `boot_emit` against `boot()`'s output once. A
  scratch mutation of `boot_emit` confirmed the check fails when it should.
  Removed the stale "never compiled / known expected failure" header text.
- This handoff's current-state sections updated to match.

All 30 shared cases agree with the firmware. One discrepancy to report,
not edited because the vectors are a shared interface: the fixture file's
`description` says duplicate keys resolve to "the first occurrence". Measured
with ArduinoJson 6.21.6, the **last** occurrence wins (see "Extra/duplicate
JSON field policy"). No case exercises duplicates, so no test is affected;
the dashboard/control side should correct that sentence.

- Commit: see the commit that adds this entry (fixture + harness + handoff).
- Changed files and interfaces: vendored fixture (30 cases), fixture
  harness (boot check), this handoff. No message shapes, mixing equations,
  or `hardware_pwm_ceiling` changed.
- Tests and results: native 52/52 pass, as above.
- Mock or physical coverage: native host only.
- Known limitations: `pio run -e esp32-s2` not attempted (declined earlier
  this session); no flashing; no hardware. The Arduino-guarded files
  (`main.cpp`, `motor_shield.cpp`, `imu_bno055.cpp`) have never been
  compiled.
- Next action: build-only `pio run -e esp32-s2`; dashboard/control to fix
  the duplicate-key sentence in the shared fixture's description.

### 2026-09-26 EDT - Request channel adopted; DC-1 to DC-3 answered; esp32-s2 build succeeds

- Merged origin/main (b37213e, cross-workstream request channel; also brings
  in ai_camera_detect.py from 994802e) as 1988d5a. The merge was clean.
- Added Requests/Responses tables above and answered DC-1, DC-2, and DC-3.
  No FW requests were filed. The duplicate-key wording in the shared
  fixture description is left as a note only, by the user's choice.
- No CLAUDE.md was added: this Claude Code environment loads AGENTS.md
  automatically (it was injected at session start), so a CLAUDE.md that
  only contains `@AGENTS.md` would load it twice.
- DC-3 command, run from a `git archive` export of 1988d5a with
  `PLATFORMIO_CORE_DIR` in scratch and nothing flashed:

      pio run -e esp32-s2

  Result: SUCCESS in 488 s (most of it the first toolchain download). All
  eight src/ files compiled with 0 warnings. RAM 4.8%, flash 20.6%. One
  package mirror was unreachable from this network and PlatformIO fell back
  to another automatically.
- At the user's explicit direction, this branch also updates AGENTS.md (two
  invariants: stale arms are ignored and accepted arms zero outputs; the
  shared serial vectors are vendored unedited and replayed by the firmware
  native tests) and changes.md (current state, active work, checkpoints,
  and a History entry). WORKSTREAMS.md normally reserves those files for
  the integrator, so expect conflicts there at merge.
- Mock or physical coverage: native host tests plus the board compile. No
  flashing or hardware testing.
- Next action: see "Next action" above.

## Entry template

### <ISO date/time with timezone> - <short task>

- Commit:
- Changed files and interfaces:
- Tests and results:
- Mock or physical coverage:
- Known limitations:
- Next action:
