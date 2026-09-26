# Docker simulation on macOS

This setup keeps Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic, and the dashboard
inside one container. Docker uses the host's native architecture, including
arm64 on Apple Silicon. Gazebo uses Mesa software rendering and a browser
desktop, so no macOS X server is needed.

Status: the ARM64 image builds, all four ROS packages build, and Gazebo spawns
the robot using Mesa llvmpipe. The existing Python suite passes in Ubuntu
(149 passed, 2 skipped). Initial runtime checks found adapter argument parsing
and Gazebo topic mismatches; end-to-end driving/navigation validation is still
in progress. Do not treat the build as a passing simulation checkpoint.

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
Focus the dashboard when using W/S, A/D, arrows, and Space. Enable driving
explicitly. Verify the Gazebo robot moves and stops before starting navigation.

The image contains a snapshot of this worktree. Rebuild after source edits and
run `up -d` again. No host repository, serial devices, Docker socket, or runtime
sockets are mounted into the container. It runs as an unprivileged user and
publishes only the dashboard and browser desktop on Mac loopback ports.

## Validation and navigation

Commands executed with Compose need the ROS overlay; the entrypoint sources it:

```bash
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim python3 -m unittest discover -s tests -v
docker compose -f ros_ws/docker/compose.yaml exec sim node --check app/rescuebot/static/dashboard.js
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 topic list
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 topic echo /odom --once
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 topic echo /scan --once
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 topic echo /imu/data --once
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
accepts RViz `/goal_pose` goals. Check manual override, Space, and obstacle
stopping as described in the workstream handoff.

Stop all container processes with:

```bash
docker compose -f ros_ws/docker/compose.yaml down
```

The healthcheck tests dashboard HTTP availability only. It does not establish
working Gazebo sensors, ROS bridges, SLAM, or navigation. Software rendering
performance on this Mac is also unvalidated.
