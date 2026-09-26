# Rescuebot ROS 2 simulation workspace

This workspace is the simulation track. It has no ESP32 serial support and
cannot command the physical motor shield.

Use Ubuntu 24.04 with ROS 2 Jazzy and Gazebo Harmonic. Install the dashboard
project into the same Python environment so the simulation command bridge can
parse the host's bounded IPC records:

```bash
cd /path/to/shellhacks2026
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
source /opt/ros/jazzy/setup.bash
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
