# Autonomy simulation handoff

Workstream: simulation autonomy
Branch: feature/autonomy-sim
Original base: test/integration at 4af9098
Current integration base: test/integration at d6a5d5f, merged in 38c686d
Worktree: /private/tmp/rescuebot-autonomy-sim
Status: Docker on macOS ARM64 runs Ubuntu 24.04/Jazzy/Harmonic. Dashboard goals turn toward the route and travel primarily forward, retaining holonomic correction. Seven-goal/8.23 m travel, a Nav2-selected divider detour, panel/cylinder stops, and regression checks pass. One final-image Gazebo startup hang recovered with restart and remains documented. Blocked-goal recovery, exploration, and physical autonomy remain unvalidated.

## User-authorized scope exception

On 2026-09-26, the user explicitly authorized continuing ROS 2, Gazebo, SLAM,
and Nav2 work in simulation now, in parallel with physical acceptance work.
This is a simulation-only exception to the deferral in AGENTS.md,
WORKSTREAMS.md, and IMPLEMENTATION_PLAN.md. The integrator should record this
decision in changes.md; this workstream does not edit the shared status or
plan files.

Gazebo/ROS output must never reach the serial transport, ESP32, or real motors.
The existing non-Gazebo application paths must retain their behavior. Scope is
ros_ws/, simulation-specific application behavior and tests, and this handoff.
No direct firmware edits, other workstream handoff edits, or merges into
test/integration or main are authorized. Push only feature/autonomy-sim.

## Checkpoints

| Commit | Coverage | Validation |
|---|---|---|
| `506f93e` | Gazebo backend, host arbitration, ROS IPC/conversion, model/world/bridges | 148 Python tests and JavaScript syntax check passed locally. |
| `225fec2` | SLAM Toolbox, Nav2, Collision Monitor, goal manager, frontier evaluation gate | Static ROS asset/safety tests and Python syntax checks passed locally. |
| `5868e16` | Previous `test/integration` merge, including the earlier wheel-direction configuration | Mecanum, autonomy, and ROS asset tests passed after that merge. Configuration superseded by the merge below. |
| `38c686d` | Merge of `origin/test/integration` at `d6a5d5f`, carrying the verified wheel configuration that replaces `e952e9a` | macOS Python regression suite: 151 discovered, 149 passed, 2 skipped; JavaScript syntax passed. No ROS/Gazebo runtime coverage. |
| `e6e5bee` | Native ARM64 Docker environment | All four ROS packages build; initial Ubuntu suite 149 passed, 2 skipped. |
| `95073de` | Gazebo model physics, sensors, bridges, and manual speed | Six actual-pose direction checks, browser keys/Space, speed, release, Stop and input expiry pass; 151 Python passes, 2 skips, one ROS pass. |
| `aaf3f88` | Runtime SLAM/Nav2, mission cancellation, matching IPC/velocity limits | Map and goal success, Stop/manual override, safe-source expiry/no rearm; 152 Python passes, 2 skips on each OS, five ROS passes. |
| `0dcfc75` | Wider simulation collision zone and reproducible obstacle/recovery checks | Rear obstacle: FootprintStop, zero filtered output and 0.0875 m clearance; goal/override/managed pause-resume pass; 152 Python passes, 2 skips, five ROS passes. |
| `a824566` | Simulation dashboard destinations, readiness/status IPC, automatic Docker navigation startup | Image/colcon build, 156 Python passes and 2 skips on each OS, seven ROS passes; live dashboard API goal/Stop/takeover/source-loss checks now pass. |
| `9be171f` | Clear-aisle default destination and completed browser acceptance | Real browser default goal succeeds; field editing/Enter submission/W takeover/Space/Stop/reload tested. Final Ubuntu regression: 156 passes, 2 optional skips. |
| `08ca7e7` | Rotate toward the route, favor forward travel, retain strafe | Three clear-aisle goals, actual heading/travel checks, Stop/takeover/source expiry; 156 Python passes, two skips, seven ROS passes. |
| `942ae00` | Nearby local path horizon, longer routes, continuous-pose acceptance, panel/cylinder stops | Final seven-goal route 8.23 m, independent 2.99 m divider detour, two inserted-obstacle stops, final safety regression; 156 Python passes/two skips on each OS and seven ROS passes. |

Publication: code checkpoints `08ca7e7` and `942ae00` and this handoff are
committed and pushed only to origin/feature/autonomy-sim, not integrated.
The working tree is clean at handoff. Expected commit identity was verified
before each commit; Git configuration and branch history were not rewritten.
The simulation control tab is closed for automated acceptance; the robot is
disarmed after each test.

## Current checkpoint

- The browser can select `--motor-backend gazebo`.
- The Gazebo backend accepts only the selected host command and sends it over
  bounded, expiring Unix datagrams to `rescuebot_sim_command_bridge`.
- The bridge converts the project convention (forward/right/clockwise) to ROS
  `Twist` convention (forward/left/counterclockwise) and publishes `/cmd_vel`.
- The dashboard exposes Start autonomy only for the Gazebo backend. The normal
  bridge backend cannot start autonomy. The new simulation goal form and
  readiness/status channel pass unit/ROS tests and live browser acceptance.
- Autonomous input is latest-only, expires after 250 ms, and disarms on
  expiry. Manual movement cancels the mission; key release does not restart it.
- `ros_ws/` contains a parameterized Xacro model, Harmonic SDF model/world,
  `ros_gz_bridge` topic map, simulated LiDAR/IMU/odometry, and `odom →
  base_link` TF bridge.
- `rescuebot_navigation` starts SLAM Toolbox, Nav2 with a rotation shim around
  holonomic DWB, Collision Monitor, and a mission manager that owns dashboard
  destinations and RViz `/goal_pose` actions. Nav2 output flows through `/cmd_vel_nav`, the velocity smoother, and
  `/cmd_vel_smoothed`; only Collision Monitor publishes `/cmd_vel_safe`.

## Interfaces

- Host-to-Gazebo IPC: `SimulationCommand` in `app/rescuebot/simulation_ipc.py`.
  It carries an advancing sequence, monotonic expiry, armed flag, and logical
  axes. It is simulation-only and has no serial field or hardware path.
- ROS-to-host IPC: `AutonomyIntent` in `app/rescuebot/autonomy.py`, carried by
  `autonomy_ipc.py`. It has a mission id, sequence, expiry, and normalized
  logical axes.
- ROS adapter input: `/cmd_vel_safe` (`geometry_msgs/Twist`). Conversion is
  `forward=vx/max_vx`, `sideways=-vy/max_vy`, `turn=-wz/max_wz`; the ROS
  adapter never mixes wheels. Runtime limits are 0.08 m/s and 0.24 rad/s,
  matching the host fixed 20% autonomy scale; manual full scale stays 0.40 m/s
  and 1.20 rad/s.

## Original static validation (superseded by Docker runtime results below)

Passed locally on macOS arm64 using the repository virtual environment:

```bash
PYTHONPATH=app .venv/bin/python -m unittest discover -s tests -v
node --check app/rescuebot/static/dashboard.js
```

Result: 150 Python tests passed (including two environment-gated integration
tests that were skipped) and JavaScript syntax passed. ROS 2, Gazebo,
and colcon are not installed on this machine, so the launch path is not yet
runtime-validated. Run the commands in `ros_ws/README.md` on Ubuntu 24.04 with
ROS 2 Jazzy and Gazebo Harmonic.

## Remaining work

1. Replace estimated chassis dimensions in the Xacro/SDF with measured values.
2. Extend the passing rear-obstacle check to front/side approaches and
   different speeds. Short goal, Stop, override, and source-expiry checks pass.
3. Extend mapping/navigation to long routes, loop closure, and varied goals.
   Dashboard destination acceptance passes. Interactive RViz selection is
   still unvalidated; browser focus-loss Stop makes the dashboard form the
   intended single-operator flow.
4. Evaluate the Jazzy-compatible frontier package in the simulator before
   writing a local frontier explorer. Do not let that block Milestone B.
5. Keep physical autonomy work separate until the integration candidate passes
   combined manual physical acceptance and is tagged.

## 2026-09-26 - Resume and integration refresh

- Resumed the existing clean branch at `386b5ac` in its separate worktree;
  preserved branch history. The last commit identity matched the expected
  team identity before the merge. No Git configuration was changed.
- Fetched origin, then merged `origin/test/integration` at `d6a5d5f` without
  conflicts, producing `38c686d`. The merge imported only the upstream changes
  in `firmware/include/chassis_config.h` and `firmware/platformio.ini`; no
  firmware changes were developed here. No application interfaces changed.
- Local environment: macOS 26.6.2, Darwin 25.6.0, arm64; Python 3.14.7 from
  the existing dashboard checkout's virtual environment. No packages or
  system-wide software were installed.
- The user selected a teammate's Ubuntu 24.04 machine for runtime validation.
  SSH host/alias, username, and installed Jazzy/Harmonic availability are still
  pending. The target OS and ROS/Gazebo installation have not been verified.
- The cross-workstream Requests/Responses channel is limited to dashboard
  and ESP32 workstreams; no request routing applies to simulation autonomy.
- Documentation discrepancy: the shared plan/workstream documents still defer
  ROS/autonomy and list only the original two workstreams. The explicit scope
  exception above governs this branch and awaits the integrator's shared-log
  update.

Commands run from `/private/tmp/rescuebot-autonomy-sim`:

```bash
git fetch origin --prune
git log -1 --format='%an <%ae>'
git merge --no-edit origin/test/integration
PYTHONPATH=app /Users/shaderahman/Documents/coding/shellhacks2026/.venv/bin/python -m unittest discover -s tests -v
node --check app/rescuebot/static/dashboard.js
```

Results at `38c686d`:

- Fetch and merge succeeded with permission to write shared Git metadata.
- The first Python run inside the restricted sandbox reported 18 socket-bind
  permission errors. Rerunning the same command with local socket access
  succeeded: `Ran 151 tests in 2.109s`, `OK (skipped=2)` (149 passed).
- The skipped tests are the browser and firmware-link integration tests,
  gated by `RESCUEBOT_INTEGRATION=1`; that flag was not enabled. No real
  serial transport, ESP32, or motors were used.
- Existing live-camera tests emitted unclosed-file ResourceWarnings; they
  passed. No camera code was modified.
- JavaScript syntax check exited 0.

Status: integration merge committed; this handoff records the accompanying
documentation checkpoint on feature/autonomy-sim. No integration into
test/integration or main. Ubuntu build, Gazebo launch/bridges, browser driving,
SLAM, Nav2 goal-following, and simulated obstacle/Stop/override behavior remain
unvalidated.

Next action: obtain SSH access details, verify Ubuntu 24.04 and installed
Jazzy/Harmonic tools, then build and run the commands in ros_ws/README.md.
Ask before any system-wide installation. Validate dashboard W/S, A/D, arrows,
release, and Space with `--motor-backend gazebo` before proceeding to SLAM
and Nav2. Keep dashboard and ROS IPC endpoints on that same Ubuntu host.

## 2026-09-26 - Switch to Docker on macOS

The user changed the validation target from a teammate's Ubuntu machine to
Docker on this Apple Silicon Mac, then explicitly approved installing and
starting Docker Desktop. Ubuntu 24.04 remains inside the container; macOS
remains the host OS. The simulation-only scope exception and physical-output
prohibition remain in force.

Container setup is under ros_ws/docker/. The dashboard, ROS IPC endpoints,
Gazebo, and navigation run in the same container. Only localhost dashboard
and browser desktop ports are published; no host devices or directories are
mounted. Runtime validation results will be recorded here after the build.

Initial container checkpoint:

- Docker Desktop 4.92.0 / Engine 29.8.0 is installed and running as
  linux/arm64. macOS has 24 GiB RAM; Docker reports 15 CPUs and approximately
  8 GiB RAM. No macOS ROS or Gazebo packages were installed.
- `docker compose -f ros_ws/docker/compose.yaml config --quiet`: passed.
- `docker compose -f ros_ws/docker/compose.yaml --progress plain build`:
  passed. `colcon build --symlink-install --event-handlers console_direct+`
  inside the image built all four packages.
- `docker compose -f ros_ws/docker/compose.yaml up -d`: started the dashboard,
  browser desktop, and Gazebo; the robot entity was created successfully.
- `docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 -m unittest discover -s tests -v`:
  151 discovered, 149 passed, 2 skipped in Ubuntu/Python 3.12. The README now
  uses a disposable `run --rm --no-deps` test container to keep test runtime
  sockets separate from the running simulator.
- `docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh glxinfo -B`:
  Mesa llvmpipe 25.2.8, OpenGL 4.5, software rendering.
- Runtime failures found: both custom adapters reject launch-added
  `--ros-args`; Gazebo subscribes to `/cmd_vel` while ros_gz publishes to
  `/model/rescuebot/cmd_vel`; odometry and scan bridge endpoints also mismatch
  actual Gazebo publishers. IMU does not publish. Fixes are in progress.

## 2026-09-26 - Gazebo manual driving validated

The Docker environment now runs the model and live ROS bridges. Fixed ROS
launch argument parsing, absolute Gazebo topic names, IMU system loading,
sensor frame IDs, and the duplicate base_link parent in TF. Real model-pose
checks exposed backward wheel axes, chassis ground contact, and missing
mecanum friction; the SDF now uses correctly aligned joints and anisotropic
wheel contact. Geometry remains estimated, pending physical measurements.

Only `app/rescuebot/gazebo_backend.py` changed in app/: Gazebo commands now
honor dashboard speed and normalize diagonal translation. Non-Gazebo paths
and all physical mixing remain unchanged.

Validation on macOS Docker / Ubuntu 24.04 ARM64:

```bash
docker compose -f ros_ws/docker/compose.yaml --progress plain build
docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_manual.py
PYTHONPATH=app /Users/shaderahman/Documents/coding/shellhacks2026/.venv/bin/python -m unittest discover -s tests -v
```

- Build: all four colcon packages succeeded. Live `/clock`, `/odom`, `/scan`,
  and `/imu/data` received; LiDAR and IMU identify their correct frames.
- The manual validator passed all six directions against **Gazebo world pose**,
  not wheel odometry alone. At 30% for 0.8 seconds: W +0.093 m, S -0.091 m,
  A +0.092 m left, D -0.098 m left; left/right rotation +0.278/-0.277 rad.
  Forward distance at 40% was 0.116 m versus 0.058 m at 20%.
  Release, Stop while W remained held, and browser-input expiry all stopped
  the model. The validator always stops on exit and requires the Gazebo backend.
- Native Chrome UI at localhost:18000: W, A/D, left/right arrows moved the
  actual model in the expected directions; Up/Down changed 40% to 50% and back;
  Space displayed DISABLED, operator_stop, and zero requested wheel outputs.
  Discrete key taps produced small motion. The test window was closed after
  stopping. No headless-browser workaround was needed.
- macOS suite: 153 discovered, 151 passed, 2 environment-gated tests skipped.
  An invocation without `PYTHONPATH=app` accidentally used the other checkout's
  editable install and failed imports; the correct command above passed.
- A real ROS subprocess test verifies launch remapping, command conversion,
  and zero velocity after IPC expiry; its colcon pytest run passed.
- Initial navigation launch failed: costmap width/height require integers;
  upstream bringup also creates a second Collision Monitor. Mapping and goal
  navigation are still being corrected and are not yet validated.

The ESP32 being attached to the Mac does not participate in these tests:
Compose grants no devices, host mounts, or serial transport access. Physical
acceptance remains entirely separate. Changes are on feature/autonomy-sim;
no integration or main merge was performed.

Ubuntu checkpoint verification with a disposable test container:

```bash
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'python3 -m unittest discover -s tests -v && node --check app/rescuebot/static/dashboard.js && cd ros_ws && colcon test --packages-select rescuebot_sim_bridge --event-handlers console_direct+ && colcon test-result --verbose'
```

Passed: 153 discovered, 151 passed, 2 skipped; JavaScript syntax; one ROS
adapter test, zero errors/failures/skips. This completes manual-driving runtime
validation for the simulation model, not physical robot acceptance.


## 2026-09-26 - SLAM and Nav2 runtime acceptance

Checkpoint after manual-driving commit `95073de`:

- SLAM now uses the upstream lifecycle launch with autostart. It publishes
  `/map` and `map -> odom`; the global costmap consumes that map.
- Explicitly launch six Nav2 lifecycle nodes so upstream docking/route servers
  and duplicate Collision Monitor nodes do not change this workstream's scope.
  Costmap dimensions use Jazzy integer parameters; Collision Monitor uses
  `points` and receives the smoother output. Every velocity interface explicitly
  uses Twist, keeping the existing host adapter contract.
- Remap Nav2's direct `/goal_pose` subscriber away from the operator topic.
  Only the mission manager receives RViz goals and owns the action. This fixed
  a duplicate goal/preemption observed during runtime validation.
- Holonomic DWB sampling includes zero angular velocity (21 samples); heading
  alignment critics no longer force a turn during strafe. Controller limits
  and adapter normalization match the existing fixed host 20% autonomy scale.
- Idle missions send only zeros through the smoother/Collision Monitor, so an
  operator can take time selecting a goal. No idle zeros are produced during
  a pending/executing goal, preserving source-loss stops. Stationary output
  is bounded to 3600 seconds of simulation time; restart navigation after that
  limit. The 250 ms input expiry itself is unchanged.
- Mission generations cancel late Nav2 acceptance after Stop and prevent an
  old action result from clearing a newer goal. Host status loss also cancels.
- Repeated forwarding of a ROS sample now preserves its original expiry rather
  than extending it. Mission changes discard the previous velocity sample.
- A second app change is limited to `create_app`'s Gazebo branch: autonomy IPC
  now shares the same runtime directory as the Gazebo bridge/ROS adapter.
  The bridge and mock selection paths retain their previous code and behavior.

Exact runtime commands (repository root):

```bash
docker compose -f ros_ws/docker/compose.yaml --progress plain build
docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh ros2 launch rescuebot_navigation navigation.launch.py
# Keep the navigation terminal running; in another terminal:
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_navigation.py
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'python3 -m unittest discover -s tests -v && node --check app/rescuebot/static/dashboard.js && cd ros_ws && colcon test --event-handlers console_direct+ && colcon test-result --verbose'
```

Results:

- All six Nav2 lifecycle nodes became active. Initial map: 104 x 117 cells,
  0.05 m resolution, 4286 known cells; map expanded as the robot moved.
- Goal (map x=0, y=-0.60) succeeded. Actual Gazebo world-pose displacement:
  x=-0.016 m, y=-0.513 m, yaw=+0.063 rad; within the configured 0.10 m goal
  tolerance. Both Nav2 and the mission manager reported success.
- Stop during a second goal canceled the action and held actual model position.
- Manual input during a third goal canceled with `manual_override`; releasing
  the key did not restart autonomy.
- Deactivating Collision Monitor during a fourth goal removed safe velocity;
  the host disarmed with `autonomy_timeout` after 0.157 seconds measured from
  lifecycle response. Reactivation did not rearm or restart the mission.
- Ubuntu Python 3.12: 154 discovered, 152 passed, two environment-gated skips.
  JavaScript syntax passed. `colcon test` completed all four packages with
  five tests, zero errors/failures/skips: actual ROS adapter launch/expiry,
  stale-sample expiry, late acceptance cancellation, old-result isolation,
  and idle-versus-executing source behavior.
- Earlier failed attempts are resolved: wrong socket directories, missing TF
  discovery wait in the validator, double goal submission, and slow DWB final
  heading convergence. No physical transport was involved.

Next action: test a new obstacle entering the Collision Monitor footprint,
then longer map/goal routes and RViz selection. Frontier selection, measured
chassis parameters, physical robot acceptance, and real autonomy remain
outside this completed short simulation acceptance. The integrator should
record the authorized scope exception and merged results in shared changes.md
and reconcile the shared implementation plan; neither was edited here.


Final packaging and publication:

- Code checkpoint `aaf3f88` was pushed only to origin/feature/autonomy-sim;
  the prior Docker and manual-driving commits were included. No history was
  rewritten and no other branch was pushed.
- The final image was rebuilt successfully and started again with
  `docker compose -f ros_ws/docker/compose.yaml up -d`. SLAM/Nav2 was launched
  with the command above. Dashboard: http://localhost:18000 ; browser desktop:
  http://localhost:16080/vnc.html?autoconnect=true&resize=scale .
- `docker inspect rescuebot-autonomy-sim-sim-1` confirms no mounted host
  directories/devices, no privileged mode, and only loopback port bindings.
  The simulator was left disarmed with autonomy inactive.
- Final macOS command with `PYTHONPATH=app`: 154 discovered, 152 passed,
  2 skipped in 2.152 seconds. `git diff --check` passed; no authored firmware
  differences exist after integration merge `38c686d`.
- The last commit identity matched the expected team identity before each
  commit. Git configuration was not changed.


## 2026-09-26 - Visible obstacle-stop acceptance

The user authorized the next simulation obstacle test and asked how to see
movement because the dashboard camera was stale. The live simulator is the
noVNC Gazebo desktop at http://localhost:16080/vnc.html?autoconnect=true&resize=scale .
The localhost:18000 dashboard uses a short detection replay, not a simulated
camera image. A stale replay banner is expected. Use two windows side by side,
with focus on the control dashboard for manual input; focus loss stops driving.
The Gazebo view was zoomed onto the robot through Entity Tree / Move To and
scroll. The user closed the simulation control tab for automated ownership;
the Pi dashboard and all physical hardware were left outside the test.

Work remains in /private/tmp/rescuebot-autonomy-sim on feature/autonomy-sim.
Fetched origin before changes; the request channel applies only to dashboard
and ESP32 workstreams, with no simulation request routing. The previously
recorded user scope exception remains in force.

Changes:

- Added `/rescuebot/collision_state` telemetry to Collision Monitor so the
  acceptance test can distinguish its FootprintStop action from planner stops.
- Added `ros_ws/docker/validate_obstacle.py`. It requires the Gazebo backend
  and unowned/disarmed dashboard, starts a mission-owned backward goal,
  inserts a uniquely named red Gazebo panel, and checks nonzero incoming
  velocity, Collision Monitor STOP, zero filtered output, actual world-pose
  stability, and geometric clearance using the model's collision dimensions.
  It always sends Stop and removes only its own panel.
- The original stop-zone half-extents (0.19 m x 0.16 m) failed the runtime
  clearance assertion. Collision Monitor did command Stop, but the final
  model/obstacle projection was at the contact boundary (-0.00000063 m).
- Widened the simulation stop-zone half-extents to 0.30 m x 0.26 m, leaving
  room for sensor, command and stopping delay outside the estimated chassis.
  No app/, firmware, serial protocol, velocity limits, or physical paths changed.
- Updated the simulation README files with live-view instructions and the
  reproducible obstacle test. These are simulation margins, not calibrated
  physical safety distances.

Commands from the worktree root (navigation.launch.py running separately):

```bash
docker compose -f ros_ws/docker/compose.yaml --progress plain build
docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh ros2 launch rescuebot_navigation navigation.launch.py
# Another terminal, with localhost:18000 control tabs closed:
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_obstacle.py
```

The actual development run copied the revised YAML/script into the existing
container and restarted only navigation, preserving the user's live Gazebo
viewport. The final Docker image was rebuilt from the worktree for reproduction.

Results with the wider zone:

- PASS: FootprintStop reported STOP; `/cmd_vel_safe` was zero while nonzero
  `/cmd_vel_smoothed` requests reached the filter.
- STOP telemetry arrived 0.392 seconds after the Gazebo creation request began.
  This includes creation/CLI latency; it is not a standalone sensor latency
  or worst-case braking bound.
- Final measured clearance: 0.0875 m. Translation drift during the stationary
  check: approximately 0.0029 m; yaw drift 0.0206 rad.
- The red panel was visible in noVNC, then removed after the test. The script
  sent Stop and released control. The first harness attempt also exposed a
  status propagation race; it now waits for ROS to confirm the current host
  mission before publishing a goal.
- A follow-up navigation check run during a concurrent Docker build tripped
  `browser_timeout` and correctly disarmed. Runtime checks should run without
  simultaneous image builds on this software-rendered Docker environment.

This validates one rear-obstacle scenario at the configured low autonomy speed.
Front/side approaches, different velocities, long routes, loop closure, measured
geometry, and physical acceptance remain unvalidated. Next: extend obstacle
coverage, then longer navigation routes. No physical autonomy is authorized.


Final validation at code checkpoint `0dcfc75`:

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_navigation.py
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'python3 -m unittest discover -s tests -v && cd ros_ws && colcon test --event-handlers console_direct+ && colcon test-result --verbose'
git diff --check
```

- The wider zone also passed ordinary Nav2 goal following, Stop, manual
  override, source expiry, and no automatic rearming.
- Recovery testing exposed a harness cleanup bug: directly toggling one
  managed node causes a delayed bond failure in Nav2's lifecycle manager.
  The harness now uses managed PAUSE/RESUME and verifies every managed node
  remains active five seconds afterward. This change is in the test only.
- A repeated browser timeout without a build showed that synchronous HTTP
  state reads/ROS spinning in the acceptance harness could starve its own
  heartbeat. Polling/spinning now runs outside the heartbeat event loop.
  The host safety deadlines were not increased or bypassed.
- Final goal succeeded; managed pause disarmed with `autonomy_timeout` 0.305 s
  after the pause request began (including lifecycle transition time). Resume
  kept the robot disarmed and all six Nav2 nodes active after the five-second
  stability check. No delayed lifecycle error remained.
- Ubuntu Python regression: 154 discovered, 152 passed, 2 environment-gated
  skips in 2.060 seconds. Four colcon packages completed, five tests passed,
  zero errors/failures/skips. Final Docker image build and diff check passed.
- The live container has the same revised configuration and test scripts;
  navigation remains running and the robot was left disarmed. Rebuilding did
  not restart the desktop or disturb the user's zoomed view.
- Obstacle protection here is in the autonomous Nav2 path. Manual dashboard
  commands still use the existing direct simulation control path and require
  operator stopping. No simulated camera video stream was added.

Status: code and this handoff committed on feature/autonomy-sim, pushed only
to that branch, not integrated. The user-authorized simulation scope exception
still applies; the integrator owns shared plan/status updates. No app/ or
firmware edits were made in this checkpoint, and no physical device was used.

## 2026-09-26 - Dashboard goal navigation

The user confirmed the movement keys work and requested the Start autonomy
workflow. The existing simulation-only scope exception remains in force.
Resumed the same separate worktree/branch and fetched origin; no new branch,
history rewrite, firmware edits, or physical transport access. The routed
dashboard/ESP32 request channel does not include this workstream.

Changes and interfaces:

- The simulation-only panel offers Enable / Start autonomy / Send goal with
  forward/right offsets of 0.1–2 m total. Negative offsets mean backward/left.
  The mission manager converts from the current map pose and keeps the current
  heading. This is selected-goal navigation, not autonomous exploration.
- Readiness, goal state, and SLAM position return to the dashboard. Readiness
  requires a fresh host status, map-to-base transform, safe velocity stream,
  and available NavigateToPose server. A start without readiness is rejected.
- New Gazebo-only WebSocket message: `navigation_goal` with numeric `forward`
  and `right`. It requires the owning browser, armed host, active mission,
  fresh matching navigation status, and no goal already pending/executing.
- New local sockets: `navigation-goal.sock` and `navigation-status.sock` in
  the existing simulation runtime directory. Datagrams are bounded to 1024
  bytes, nonblocking, and expire after 250 ms (goal) / 500 ms (status). Goals
  carry a mission and request id; old, duplicate, stopped, or expired requests
  cannot start a new mission. ROS stays outside the dashboard process.
- Stop/Space still cancels and disarms; manual movement still cancels without
  resuming on release. W/A/S/D and Space work while editing numeric fields;
  arrows edit those fields while focused. Other keyboard handling is unchanged.
- Docker now starts SLAM/Nav2 with the existing simulation/dashboard services.
  Do not launch a second navigation stack in the same container.
- app/ changes are confined to new simulation IPC, guarded Gazebo service/web
  branches, and a hidden-by-default simulation panel. Bridge/mock state and
  command paths, serial protocol, mixing, firmware, and deadlines are unchanged.
  Integrator: reconcile this accepted setup/UI decision in shared README,
  IMPLEMENTATION_PLAN.md, and changes.md when integrating this branch.

Passing pre-runtime commands from /private/tmp/rescuebot-autonomy-sim:

```bash
PYTHONPATH=app /Users/shaderahman/Documents/coding/shellhacks2026/.venv/bin/python -m unittest discover -s tests -v
node --check app/rescuebot/static/dashboard.js
docker compose -f ros_ws/docker/compose.yaml --progress plain build
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'python3 -m unittest discover -s tests -v && node --check app/rescuebot/static/dashboard.js && cd ros_ws && colcon test --event-handlers console_direct+ && colcon test-result --verbose'
git diff --check
```

Environment remains macOS ARM64 / Docker / Ubuntu 24.04 / Jazzy / Harmonic.
Mac: 158 discovered, 156 passed, 2 optional integration skips (final run 1.572 s).
Ubuntu: 158 discovered, 156 passed, 2 optional integration skips.
All four colcon packages built; seven ROS tests passed, no errors/failures/skips.
Tests cover freshness, bounds, ownership, mission identity, non-Gazebo
rejection, rotated right-axis conversion, duplicate suppression, and goals
arriving after Stop or a mission change. The initial restricted Mac test
attempt could not bind test sockets; the normal approved rerun passed.

At this checkpoint the live container still runs the previous version while
the user's simulation dashboard owns control. A request to close that tab is
pending. Automatic approval review rejected an extra detached simulation
container due to resource/duplicate-service risk; no extra instance was started.
Next: update the existing container after release, verify automatic readiness,
run `validate_navigation.py --dashboard-goals`, and exercise the form in the
browser. This checkpoint does not claim live validation of the new goal form.
Code checkpoint: `a824566`; the final image was rebuilt and the Ubuntu/ROS
suite rerun successfully after the final input-boundary and stale-request
cleanup fixes. The simulator remains stopped/disarmed on the prior runtime.
No physical tests ran. Only feature/autonomy-sim is published.

## 2026-09-26 - Live dashboard acceptance completed

The user authorized closing the local simulation control tab and updating and
testing the existing container. On inspection that tab was already closed;
the API confirmed no owner and disarmed output. The remaining dashboard tab
was the Pi dashboard and was left unchanged. Fetched origin and confirmed the
same clean feature/autonomy-sim worktree before proceeding. There are no
cross-workstream requests routed to simulation.

Updated the existing Compose service, without starting a duplicate simulator.
The previous automatic approval rejection is resolved by this existing-service
workflow. Environment remains macOS ARM64 with Ubuntu 24.04, ROS 2 Jazzy and
Gazebo Harmonic in native ARM64 Docker; no system-wide installation or physical
hardware access occurred.

Commands from /private/tmp/rescuebot-autonomy-sim:

```bash
git fetch origin --prune
docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_navigation.py --dashboard-goals
# After correcting the default destination:
docker compose -f ros_ws/docker/compose.yaml --progress plain build
docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'python3 -m unittest discover -s tests -v && node --check app/rescuebot/static/dashboard.js'
git diff --check
```

Results:

- SLAM/Nav2 started automatically and reached readiness. The API test saw a
  104x117 map with 4286 known cells. The WebSocket goal completed with actual
  Gazebo model displacement approximately (0.0074, -0.5102) m and -0.1248 rad.
- API Stop held the actual model stationary. Manual override canceled without
  resuming on release. Managed Nav2 pause disarmed after 0.356 s measured from
  the pause request; all six nodes remained active after managed resume, with
  no automatic rearm. These measurements include lifecycle/test overhead.
- Native Chrome at localhost:18000 exercised Enable, Start autonomy and Send
  goal. The original default (Forward 0.5, Right 0) pointed too near the divider.
  Logs confirmed `Robot to stop due to FootprintStop polygon`, followed by
  Nav2 failed-progress retries. Space canceled and disarmed correctly.
- Corrected the simulation-only default/example to Forward 0, Right 0.5, into
  the clear aisle. Added guidance to select open destinations and manually
  leave a blocked area before restarting. No controller tuning or stop-zone
  reduction was made. This is the only application change in this follow-up.
- After a fresh start and browser hard refresh, the corrected default completed
  and visibly displayed `Goal reached — choose another`. Reported SLAM pose
  was x=-0.0155 m, y=-0.4327 m, yaw=-0.1290 rad, within configured goal tolerance.
- With the distance field focused, Down changed Right from 0.5 to 0.4 without
  changing control mode/speed. Enter submitted that second goal. W canceled it
  while executing; the API reported `manual_override`, manual source, canceled
  navigation, and zero output after release. It did not resume automatically.
- The Stop button disarmed; refreshing/reconnecting stayed disarmed. The final
  dashboard is open with Forward 0 / Right 0.5 and navigation ready.
- Final image/colcon build succeeded. Final Ubuntu Python regression: 158
  discovered, 156 passed, 2 optional integration skips in 2.020 s; JavaScript
  syntax and diff checks passed. The seven ROS tests passed at a824566; ROS
  code did not change in 9be171f and they were not unnecessarily repeated.

Local logs: /private/tmp/rescuebot-dashboard-navigation.log (API acceptance),
/private/tmp/rescuebot-dashboard-browser.log (first browser run and confirmed
FootprintStop), /private/tmp/rescuebot-dashboard-clear-goal-build.log, and
/private/tmp/rescuebot-dashboard-final-regression.log. Logs/build products are
not committed. The Chrome page needed Cmd+Shift+R to discard its cached HTML
after rebuilding; documented defaults match the served and displayed page.

Status: code checkpoint 9be171f and this handoff are committed and pushed only
to feature/autonomy-sim, not integrated. The expected last-commit identity was
checked before commits; Git configuration was not changed. The simulation-only
scope exception remains in force for the integrator to record in changes.md.

Next: validate longer selected routes around the divider and varied obstacle
approaches before evaluating automatic exploration. Arbitrary destinations
near walls can still invoke Collision Monitor and Nav2 retries; this form does
not show a map or validate destination clearance. Long missions, loop closure,
interactive RViz goals, frontier exploration, measured geometry, and physical
autonomy remain unvalidated. The camera panel remains a short detection replay,
not a live simulated camera; watch the separate Gazebo desktop for motion.

## 2026-09-26 — Face the route before travel

Accepted user decision: prioritize facing the selected route and driving
forward, retaining strafe when needed; then test longer routes and varied
obstacles. Automatic exploration and physical autonomy are future validation
stages, not enabled here. The integrator should reconcile this decision with
the shared plan and log the existing user-authorized simulation-only exception.

The previous configuration disabled PathAlign/GoalAlign and preserved the
initial goal heading, encouraging the observed shuffle. Dashboard destinations
now set final orientation to the bearing from the starting pose to the goal.
A Nav2 rotation shim aligns with the path before DWB takes over; path/goal
alignment and a modest PreferForward critic favor forward travel without
removing lateral velocity samples. PoseProgressChecker counts turning as
progress. All existing velocity limits, Collision Monitor geometry, and host
Stop/expiry behavior remain unchanged. Explicit ROS goal orientations still
pass through. The only app change is simulation-panel explanatory text.

Design reference: the official [Jazzy rotation-shim documentation](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/controller_plugins/configuring_rotation_shim_controller/).
The installed Jazzy package already supplies this plugin; its dependency is
now explicit. No host software was installed. Host: macOS 26.6.2 ARM64;
container: Ubuntu 24.04 ARM64, Python 3.12.3, Jazzy, Harmonic, Mesa software
rendering. Only the existing isolated Docker simulator was updated.

Exact commands from /private/tmp/rescuebot-autonomy-sim (Docker CLI is
/Users/shaderahman/.docker/bin/docker on this Mac):

```bash
docker compose -f ros_ws/docker/compose.yaml --progress plain build
docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_heading.py
docker compose -f ros_ws/docker/compose.yaml restart sim
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_navigation.py --dashboard-goals
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'python3 -m unittest discover -s tests -v && node --check app/rescuebot/static/dashboard.js && cd ros_ws && colcon test --event-handlers console_direct+ && colcon test-result --verbose'
PYTHONPATH=app /Users/shaderahman/Documents/coding/shellhacks2026/.venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Results:

- All four ROS packages built. Ubuntu and macOS each discovered 158 Python
  tests: 156 passed, two optional integration skips. All seven ROS tests and
  JavaScript syntax passed. The ROS goal-conversion test checks final heading
  as well as rotated coordinates.
- Three dashboard goals at map (0,-0.6), (-0.7,-0.6), (-0.7,0.5) succeeded in
  14.47, 16.35, and 24.06 s. Ground-truth forward travel shares were 99.29%,
  98.90%, and 98.95%. Position errors were 0.089, 0.091, and 0.092 m.
  Pre-alignment translation was below 0.0004 m; heading errors at 0.08 m of
  travel were 0.127, 0.002, and 0.089 rad. Lateral correction remained visible
  in the measured trajectory. Metrics use Gazebo model poses, not wheel odometry.
- A first test attempt exposed multiple queued JSON records from `gz topic`
  despite `-n 1`; the pose reader now decodes the first complete record.
  The corrected test was rerun from a fresh simulation and passed.
- Live Stop holds position; manual takeover cancels with no resume on release.
  Managed Nav2 pause disarmed in 0.358 s, including lifecycle/test overhead.
  All six Nav2 nodes remained active after resume; the host stayed disarmed.
- Logs: /private/tmp/rescuebot-heading.log, rescuebot-facing-build.log,
  rescuebot-facing-regression.log, rescuebot-facing-python.log, and
  rescuebot-facing-safety.log. Test/build artifacts are not committed.

Next: run the optional longer route, including a reversal and passing the
world divider; adapt obstacle insertion acceptance to forward-facing motion
and vary obstacle shape/approach. No exploration or physical acceptance is
claimed. The previous rear-panel result remains historical coverage until
that validator is updated for the new controller.

## 2026-09-26 — Longer routes, divider detour, and varied obstacle stops

Following the user's requested order, the forward-facing behavior at 08ca7e7
was first validated and pushed, then longer selected routes and varied
obstacles were exercised. This continues the same simulation-only scope
exception; automatic exploration and physical autonomy have not been enabled.

A seven-destination route crossed the lower opening and continued up the far
side of the divider. Before the final horizon adjustment it covered 8.174 m
in 175.01 s of goal execution, with at least 98.48% forward travel on every
leg, maximum position error 0.0965 m, and minimum conservative wall/divider
clearance 0.1721 m. Those are actual Gazebo model poses. The clearance estimate
uses a 0.20 m circumscribed robot radius including the estimated wheel spheres.

A separate single dashboard goal at map (1.55, 0) exposed a local-controller
stall: the global planner found a path around the divider, but the robot
remained near the start for the 100 s acceptance deadline. DWB's goal-distance
scoring considered a distant point across the wall. The controller now prunes
the local path to a 0.60 m forward horizon with a 0.30 m rear pruning distance,
so it scores progress along a nearby segment of the planned detour. No stop
zone, velocity limit, transport, or application logic changed in this fix.
Reference: [Nav2's DWB path transformation](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_dwb_controller/dwb_core/src/dwb_local_planner.cpp)
and [distance scoring](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_dwb_controller/dwb_critics/src/map_grid.cpp).

The same single destination then succeeded in 54.37 s over a 2.990 m path:
98.30% forward travel, 0.0827 m destination error, 0.1173 rad final-heading
error, and 0.1251 m minimum conservative clearance. No operator waypoints were
supplied in this case. The test does not compare initial heading with the
straight goal bearing on a detour, since that would point through the wall.

Obstacle acceptance now waits for forward travel after turning toward the
goal. The safety rectangle is unchanged. Two cases passed from fresh starts:

| Approach and inserted object | FootprintStop observed after create request | Geometric clearance after stopping | Drift over 0.8 s |
|---|---|---|---|
| 180-degree goal, 0.02 x 0.50 m panel | 0.432 s | 0.0917 m | Below 1 nm |
| -135-degree goal, radius 0.07 m cylinder | 0.400 s | 0.1051 m | Zero at reported precision |

Both runs required a nonzero incoming command, zero filtered output, a
FootprintStop event, and more than 5 cm clearance. Times include Gazebo service
and test overhead; they are not isolated safety-controller latency claims.
Each run sent Stop and removed only its uniquely named obstacle. These cases
validate stopping, not automatic recovery or rerouting around a newly inserted
obstacle. The old backward-travel panel result remains historical evidence.

The first longer run encountered a nonzero exit from one of many repeated
`gz topic -n 1` pose queries after four successful goals. The validator stopped
the robot. Longer acceptance now uses a single continuous Gazebo pose stream,
a bounded latest value, and a 500 ms freshness check, avoiding repeated CLI
launches. HTTP and ROS polling do not block the 50 ms control heartbeat.

Exact additional commands, each live scenario starting from a fresh simulator
with the localhost:18000 control tab closed:

```bash
docker compose -f ros_ws/docker/compose.yaml restart sim
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_heading.py --long-routes
docker compose -f ros_ws/docker/compose.yaml restart sim
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_heading.py --detour
docker compose -f ros_ws/docker/compose.yaml restart sim
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_obstacle.py
docker compose -f ros_ws/docker/compose.yaml restart sim
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_obstacle.py --shape cylinder --heading -135
docker compose -f ros_ws/docker/compose.yaml --progress plain build
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'python3 -m unittest discover -s tests -v && node --check app/rescuebot/static/dashboard.js && cd ros_ws && colcon test --event-handlers console_direct+ && colcon test-result --verbose'
docker compose -f ros_ws/docker/compose.yaml up -d
```

The final image builds all four ROS packages. Ubuntu and macOS each report
158 Python tests: 156 passed, two optional integration skips (browser and
firmware-link scenarios gated by RESCUEBOT_INTEGRATION). All seven ROS tests,
JavaScript syntax, Python syntax, and diff checks pass. No firmware, shared
plan/log, other handoff, or non-Gazebo application path was edited.

One final image startup stalled before Gazebo published the world/model:
`ros_gz_sim create` repeatedly requested world names, odom was absent, and the
initial pose check failed before claiming or enabling control. The dashboard
correctly stayed disarmed with navigation unavailable. `docker compose -f
ros_ws/docker/compose.yaml restart sim` recovered readiness. No host install or
configuration change was made; the startup root cause is unresolved. Capture
`docker compose -f ros_ws/docker/compose.yaml logs sim` if it recurs, and wait
for dashboard navigation Ready before running acceptance. Evidence is in
/private/tmp/rescuebot-final-startup.log and rescuebot-final-startup-test.log.

Final rebuilt-image route acceptance passed after readiness recovery:
seven goals, 8.229 m actual travel in 173.48 s of goal execution, forward
travel share at least 97.66% on each leg, maximum destination error 0.0939 m,
maximum final-heading error 0.1477 rad, minimum wall/divider clearance 0.1665 m,
and less than 0.00036 m translation before initial alignment. This run includes
the 0.60 m horizon fix and the continuous-pose/clearance validator.

Development tuning used `docker cp` for changed YAML and validator scripts;
the final image was rebuilt from the worktree before repeated acceptance.
Local evidence: /private/tmp/rescuebot-route-final-long.log,
rescuebot-long-routes.log, rescuebot-detour-before-horizon.log,
rescuebot-detour.log, rescuebot-panel.log, rescuebot-cylinder.log,
rescuebot-route-final-build.log, rescuebot-route-final-regression.log,
and rescuebot-route-final-mac-tests.log. Generated logs are not committed.

Remaining validation: newly blocked-goal recovery/rerouting, unreachable goals,
long-duration SLAM loop closure, automatic exploration, interactive RViz goal
entry, measured hardware geometry, and physical autonomy. The next concrete
step is to validate blocked-goal cancellation/recovery and dynamic rerouting
before evaluating automatic exploration in simulation. Physical autonomy still
requires separate hardware/physical acceptance; no ROS output is connected to
serial, the ESP32, or real motors. Non-Gazebo behavior and firmware are unchanged.

Final-image safety regression also passed:

```bash
docker compose -f ros_ws/docker/compose.yaml restart sim
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_navigation.py --dashboard-goals
```

It completed the dashboard goal with actual displacement (0.0114, -0.5069) m
and -1.5759 rad, held the model stationary after Stop, canceled on manual
input without resuming on release, and disarmed 0.310 s after managed Nav2
pause (including lifecycle/test overhead). All six nodes remained active
after resume and the host did not automatically rearm. Evidence:
/private/tmp/rescuebot-route-final-safety.log. The live final image is left
running with navigation ready, driving disarmed, and the control tab closed.
