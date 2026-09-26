# Autonomy simulation handoff

Workstream: simulation autonomy
Branch: feature/autonomy-sim
Original base: test/integration at 4af9098
Current integration base: test/integration at d6a5d5f, merged in 38c686d
Worktree: /private/tmp/rescuebot-autonomy-sim
Status: Milestones A and B are configured; local regression checks pass after the integration merge. Awaiting access to the selected teammate's Ubuntu machine for Jazzy/Harmonic validation.

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
  `/goal_pose` actions. Nav2 output is remapped to `/cmd_vel_nav`; only
  Collision Monitor publishes `/cmd_vel_safe` for the autonomy adapter.

## Interfaces

- Host-to-Gazebo IPC: `SimulationCommand` in `app/rescuebot/simulation_ipc.py`.
  It carries an advancing sequence, monotonic expiry, armed flag, and logical
  axes. It is simulation-only and has no serial field or hardware path.
- ROS-to-host IPC: `AutonomyIntent` in `app/rescuebot/autonomy.py`, carried by
  `autonomy_ipc.py`. It has a mission id, sequence, expiry, and normalized
  logical axes.
- ROS adapter input: `/cmd_vel_safe` (`geometry_msgs/Twist`). Conversion is
  `forward=vx/max_vx`, `sideways=-vy/max_vy`, `turn=-wz/max_wz`; the ROS
  adapter never mixes wheels.

## Validation

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
2. Run Milestone A launch validation: browser manual motion, `/scan`,
   `/imu/data`, `/odom`, and TF.
3. Run Milestone B validation: mapping, goal navigation, Collision Monitor,
   host expiry, and manual override while a goal is active.
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
