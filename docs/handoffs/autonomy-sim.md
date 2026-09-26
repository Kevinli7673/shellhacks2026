# Autonomy simulation handoff

Workstream: simulation autonomy
Branch: feature/autonomy-sim
Base: test/integration at 4af9098
Status: Milestones A and B are configured; awaiting Ubuntu Jazzy/Harmonic validation.

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

Result: 148 Python tests passed and JavaScript syntax passed. ROS 2, Gazebo,
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
