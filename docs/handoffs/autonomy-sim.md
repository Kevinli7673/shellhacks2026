# Autonomy simulation handoff

Workstream: simulation autonomy
Branch: feature/autonomy-sim
Original base: test/integration at 4af9098
Current integration base: test/integration at d6a5d5f, merged in 38c686d
Worktree: /private/tmp/rescuebot-autonomy-sim
Status: Docker on macOS ARM64 runs Ubuntu 24.04/Jazzy/Harmonic. Milestone A manual driving and live bridges pass. Milestone B SLAM map, Nav2 goal success, Stop/manual override, and safe-source expiry pass; obstacle-entry and long-mission validation remain.

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

Publication: runtime code through `aaf3f88` is committed and pushed to
origin/feature/autonomy-sim. The branch has not been integrated. This handoff
checkpoint is committed on the same branch. Working tree is clean at handoff.

## Current checkpoint

- The browser can select `--motor-backend gazebo`.
- The Gazebo backend accepts only the selected host command and sends it over
  bounded, expiring Unix datagrams to `rescuebot_sim_command_bridge`.
- The bridge converts the project convention (forward/right/clockwise) to ROS
  `Twist` convention (forward/left/counterclockwise) and publishes `/cmd_vel`.
- The dashboard exposes Start autonomy only for the Gazebo backend. The normal
  bridge backend cannot start autonomy.
- Autonomous input is latest-only, expires after 250 ms, and disarms on
  expiry. Manual movement cancels the mission; key release does not restart it.
- `ros_ws/` contains a parameterized Xacro model, Harmonic SDF model/world,
  `ros_gz_bridge` topic map, simulated LiDAR/IMU/odometry, and `odom →
  base_link` TF bridge.
- `rescuebot_navigation` starts SLAM Toolbox, Nav2 with a holonomic DWB
  controller, Collision Monitor, and a mission manager that owns RViz
  `/goal_pose` actions. Nav2 output flows through `/cmd_vel_nav`, the velocity smoother, and
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
2. Validate Collision Monitor with a newly introduced simulated obstacle;
   short goal, Stop, override, and source-expiry checks now pass.
3. Extend mapping/navigation to long routes, loop closure, and varied goals.
   Verify RViz goal selection interactively; runtime goal tests use `/goal_pose`.
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
