# Docker simulation on macOS

This setup keeps Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic, and the dashboard
inside one container. Docker uses the host's native architecture, including
arm64 on Apple Silicon. Gazebo uses Mesa software rendering and a browser
desktop, so no macOS X server is needed.

Status: all four ROS packages build on ARM64. Gazebo sensors, bridges, manual
dashboard keys, speed adjustment, release, Space, and input expiry have been
validated against the actual model pose. A newly inserted Gazebo obstacle
also triggers Collision Monitor and leaves measured clearance. SLAM publishes
a map, Nav2 completes a selected goal, and Stop/manual override/source loss cancel or disarm safely.
The Ubuntu suite reports 154 tests (152 pass, 2 environment-gated skips), and
all five ROS tests pass. This is simulation coverage only.

## Build and start

Install and start Docker Desktop for Apple Silicon using the official
[Mac instructions](https://docs.docker.com/desktop/setup/install/mac-install/).
System-wide installation requires the operator's permission. From the
feature/autonomy-sim repository root:

```bash
docker version
docker compose -f ros_ws/docker/compose.yaml build
docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml logs -f sim
```

Open the dashboard at <http://localhost:18000> and the Gazebo desktop at
<http://localhost:16080/vnc.html?autoconnect=true&resize=scale>.
The dashboard camera uses a short detection replay fixture. **Camera stale is
expected:** that panel is not a live simulator camera. Watch the orange robot
in the Gazebo desktop instead. Use separate windows side by side; keep focus
on the dashboard for W/S, A/D, arrows, and Space. Losing dashboard focus stops
driving. Enable driving explicitly after returning.

To get a closer view, right-click `rescuebot` in Gazebo's Entity Tree, select
**Move To**, then scroll up over the robot. Verify visible movement and stops
before starting navigation.

The image contains a snapshot of this worktree. Rebuild after source edits and
run `up -d` again. No host repository, serial devices, Docker socket, or runtime
sockets are mounted into the container. It runs as an unprivileged user and
publishes only the dashboard and browser desktop on Mac loopback ports.

## Validation and navigation

Commands executed with Compose need the ROS overlay; the entrypoint sources it:

```bash
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim python3 -m unittest discover -s tests -v
docker compose -f ros_ws/docker/compose.yaml exec sim node --check app/rescuebot/static/dashboard.js
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 topic list --no-daemon --spin-time 3
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 topic echo /odom --once
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 topic echo /scan --once
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 topic echo /imu/data --once
```

With other dashboard tabs closed, run the motion/Stop/expiry acceptance check:

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_manual.py
```

After manual driving and bridge validation, start SLAM/Nav2 in another terminal:

```bash
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 launch rescuebot_navigation navigation.launch.py
```

Open RViz on the browser desktop from another terminal:

```bash
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 run rviz2 rviz2 --ros-args -p use_sim_time:=true
```

Verify map, scan, odometry, and TF before selecting goals. The dashboard must
have driving enabled and Start autonomy selected. The existing mission manager
accepts RViz `/goal_pose` goals. Stop the current goal before choosing another.
Manual movement or Space
cancels the mission. The fixed autonomy speed limit is 20%, independent of the
manual speed selector. See [the workspace guide](../README.md) for velocity
limits and the idle mission timeout.

For repeatable goal, Stop, override, and safe-source-loss validation, close
other dashboard tabs, use a fresh simulation, start navigation, then run:

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_navigation.py
```

This publishes `/goal_pose` through the mission manager, checks Nav2 success
and actual Gazebo model travel, and temporarily pauses/resumes Nav2 through
its lifecycle manager to test source expiry and restoration without automatic
rearming. The script always sends Stop.

To demonstrate obstacle stopping, keep the Gazebo desktop open and close
control dashboard tabs so the test can own the simulated robot:

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_obstacle.py
```

The robot starts a backward Nav2 goal. A red panel appears inside the safety
zone. The check requires a `FootprintStop` event, zero filtered velocity,
stationary model pose, and more than 5 cm of geometric clearance. It sends
Stop and removes its own uniquely named panel on exit. This is one simulated
rear-obstacle scenario; it does not establish all-direction physical safety.

Run the ROS regression tests in a separate container:

```bash
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'cd ros_ws && colcon test --event-handlers console_direct+ && colcon test-result --verbose'
```

Stop all container processes with:

```bash
docker compose -f ros_ws/docker/compose.yaml down
```

The healthcheck tests dashboard HTTP availability only. It does not establish
working Gazebo sensors, ROS bridges, SLAM, or navigation. Software rendering
works on this Mac; long missions, varied obstacle approaches,
and map accuracy against measured geometry remain unvalidated. The short
replay fixture expires quickly, so a stale camera banner is expected and does
not indicate a motor or ROS failure.
