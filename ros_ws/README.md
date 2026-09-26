# Rescuebot ROS 2 simulation workspace

This workspace is the simulation track. It has no ESP32 serial support and
cannot command the physical motor shield.

For the macOS container setup and its validation status, see
[Docker simulation](docker/README.md).

Use Ubuntu 24.04 with ROS 2 Jazzy and Gazebo Harmonic. Install the dashboard
project into the same Python environment so the simulation command bridge can
parse the host's bounded IPC records:

```bash
cd /path/to/shellhacks2026
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
source /opt/ros/jazzy/setup.bash
export PYTHONPATH="$PWD/app${PYTHONPATH:+:$PYTHONPATH}"
cd ros_ws
colcon build --symlink-install
source install/setup.bash
```

Start the simulator first:

```bash
ros2 launch rescuebot_gazebo sim.launch.py
```

In another terminal, start the existing dashboard with the simulation-only
backend:

```bash
cd /path/to/shellhacks2026
source .venv/bin/activate
rescuebot-dashboard --motor-backend gazebo --camera-backend replay \
  --replay-path fixtures/detections/person_appears_disappears.jsonl
```

Open the dashboard, claim control, enable driving, and use W/S, A/D, and
Left/Right arrows. The command path is:

```text
browser → RobotControlService → GazeboMotorBackend → Unix datagram
        → rescuebot_sim_command_bridge → /cmd_vel → Gazebo MecanumDrive
```

The simulator publishes `/scan`, `/imu/data`, `/odom`, and the `odom →
base_link` transform. `robot_state_publisher` publishes the chassis and sensor
frames. Replace the estimates in `rescuebot_description/config/measurements.yaml`
and the matching SDF values before using simulation results for physical tuning.

## Validation

Run Python behavior tests from the repository root:

```bash
PYTHONPATH=app python3 -m unittest discover -s tests -v
```

On Ubuntu with ROS installed, also run:

```bash
cd ros_ws
colcon test --event-handlers console_direct+
colcon test-result --verbose
```

Gazebo must show the robot responding to browser inputs before moving on to
SLAM or Nav2. Simulation success does not authorize physical autonomy.

## Mapping and manually selected goals

After the simulator is running, launch the navigation stack:

```bash
ros2 launch rescuebot_navigation navigation.launch.py
```

The full simulation flow is:

```text
Nav2 → /cmd_vel_nav → velocity smoother → /cmd_vel_smoothed
     → Collision Monitor → /cmd_vel_safe
     → rescuebot_sim_autonomy_adapter → RobotControlService
     → GazeboMotorBackend → /cmd_vel → Gazebo MecanumDrive
```

The operator must click **Enable driving**, then **Start autonomy**, before a
goal can run. In RViz, use **2D Goal Pose** to publish `/goal_pose`; the
mission manager owns the corresponding Nav2 action. Any manual movement
cancels the mission, and releasing the key does not resume it. Stop the current
goal before selecting another. With no goal, the mission manager sends only
zero velocity through the same safety filter. It stops doing so during a goal;
missing controller output therefore still expires. A stationary mission has a
one-hour simulated-time limit before Collision Monitor stops its zero output
and host expiry disarms it.

Nav2 is configured for the host's fixed 20% autonomy limit: 0.08 m/s planar
speed and 0.24 rad/s rotation. The adapter normalization matches those limits;
the manual command bridge retains its 0.40 m/s and 1.20 rad/s full-scale values.
Do not independently change these three limits. Dashboard Up/Down affects
manual speed; autonomous speed remains the configured host limit.

Before attempting frontier exploration, verify that SLAM Toolbox supplies the
`map → odom` transform, the simulation bridge supplies `odom → base_link`, and
Collision Monitor stops motion when a simulated obstacle enters its safety
zone. The validated stop-zone half-extents are 0.30 m forward/back and 0.26 m
left/right, including clearance beyond the estimated chassis. These simulation
values need measured geometry and braking validation before physical use.
