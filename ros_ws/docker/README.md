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
Forward-facing travel and a seven-goal route around the divider have also
passed ground-truth pose checks. The controller uses a 0.60 m local path
horizon so goals across a wall follow the planned detour instead of stalling.
The Ubuntu suite reports 158 tests (156 pass, 2 environment-gated skips), and
all seven ROS tests pass. This is simulation coverage only.

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

SLAM/Nav2 now starts with the container. Do not launch a second copy. After
checking manual driving, use the **Simulation autonomy** panel on the dashboard:

1. Wait for **Ready**, then click **Enable driving** and **Start autonomy**.
2. Enter a nearby destination in metres: positive **Forward** moves forward,
   positive **Right** moves right; negative values move backward or left.
   The combined distance must be 0.1–2 m. Try Forward `0`, Right `0.5` first
   from the initial spawn, into the open aisle. The offset uses the robot's
   heading when sent. Choose a destination with clearance from walls; the
   divider is directly ahead of the spawn, so a forward goal can be blocked
   by Collision Monitor. If navigation stops at an obstacle, cancel the goal
   and use manual control to move back into clear space before restarting.
3. Click **Send goal**. The panel reports sending, navigating, and goal reached.
   Nav2 turns toward the route, drives primarily forward, and strafes for
   corrections. It finishes facing the bearing from the starting position to
   the destination. You can send another destination after the previous goal
   finishes.
4. **Stop** or **Space** cancels the mission and disarms. W/A/S/D takes manual
   control immediately; releasing the key does not restart autonomy.

After Stop, repeat **Enable driving → Start autonomy → Send goal**. Enabling
driving alone does not restart the canceled mission. The form shows the
combined straight-line distance as you edit: Forward `1.5`, Right `1.5` is
`2.12 m`, which exceeds the `2 m` limit. Send stays disabled until the values
are valid. Cmd+A / Ctrl+A selects a number inside these simulation fields;
unmodified A still takes manual control.

This is operator-selected goal navigation, not automatic exploration. The
panel shows SLAM position; the separate Gazebo desktop remains the live view.
Keep the dashboard focused while watching in another window. Arrow keys edit
the numeric fields while those fields have focus; W/A/S/D and Space retain
their takeover/Stop behavior. With a field unfocused, arrows retain normal
driving/speed behavior.

To measure heading and travel against Gazebo's actual model pose, close the
control tab and run the following from a fresh container start. The first
command checks three clear-aisle goals with right-angle turns. The optional
second command adds a reversal and a longer route around the divider; restart the simulation
before each run so it begins at the spawn. The validator requires a fresh
spawn, reads a continuous stream of actual model poses, and checks forward
travel, final heading, destination error, and wall/divider clearance. Initial
alignment is checked against the goal bearing only for the clear route legs;
a detour must face its path rather than the direct line through the wall.

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_heading.py
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_heading.py --long-routes
# From another fresh start: Nav2 chooses its own path around the divider.
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_heading.py --detour
```

If readiness does not appear, inspect `docker compose -f
ros_ws/docker/compose.yaml logs --tail 150 sim`. Readiness requires the Nav2
action server, a fresh map pose, Collision Monitor output, and host status.
Stopping or reconnecting never automatically rearms.

Open RViz on the browser desktop from another terminal:

```bash
docker compose -f ros_ws/docker/compose.yaml exec sim bash ros_ws/docker/entrypoint.sh ros2 run rviz2 rviz2 --ros-args -p use_sim_time:=true
```

The existing mission manager also accepts RViz `/goal_pose` goals. The dashboard
must have driving enabled and Start autonomy selected. Switching focus from
the dashboard to RViz stops the mission, so the dashboard form is the supported
single-operator browser workflow. Stop an executing goal before choosing another.
Manual movement or Space
cancels the mission. The fixed autonomy speed limit is 20%, independent of the
manual speed selector. See [the workspace guide](../README.md) for velocity
limits and the idle mission timeout.

For repeatable goal, Stop, override, and safe-source-loss validation, close
other dashboard tabs, use a fresh simulation, then run:

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_navigation.py
# The same acceptance checks through the dashboard destination API:
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_navigation.py --dashboard-goals
```

The default publishes `/goal_pose`; `--dashboard-goals` sends the new WebSocket
destination message. Both use the mission manager, check Nav2 success
and actual Gazebo model travel, and temporarily pauses/resumes Nav2 through
its lifecycle manager to test source expiry and restoration without automatic
rearming. The script always sends Stop.

To demonstrate obstacle stopping, keep the Gazebo desktop open and close
control dashboard tabs so the test can own the simulated robot. Begin each
case from a fresh simulation (`docker compose -f ros_ws/docker/compose.yaml
restart sim`) and wait for navigation readiness:

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_obstacle.py
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_obstacle.py --shape cylinder --heading -135
```

The robot turns toward its goal and drives forward. A red panel or cylinder
appears inside the safety zone ahead of it. The check requires a `FootprintStop`
event, zero filtered velocity, stationary model pose, and more than 5 cm of
geometric clearance. It sends Stop and removes its own uniquely named obstacle
on exit. These are simulated stopping scenarios, not obstacle detour or
physical-safety acceptance.

Run the ROS regression tests in a separate container:

```bash
docker compose -f ros_ws/docker/compose.yaml run --rm --no-deps sim bash -c 'cd ros_ws && colcon test --event-handlers console_direct+ && colcon test-result --verbose'
```

Stop all container processes with:

```bash
docker compose -f ros_ws/docker/compose.yaml down
```

## Search-and-return demo

Select the obstacle house in the existing container (the original maze remains
the default and is used by the older driving/obstacle validators):

```bash
docker compose -f ros_ws/docker/compose.yaml build
RESCUEBOT_WORLD=search_house.sdf docker compose -f ros_ws/docker/compose.yaml up -d
```

Open the [dashboard](http://localhost:18000) and
[Gazebo view](http://localhost:16080/vnc.html?autoconnect=true&resize=scale).
Keep the dashboard focused while observing Gazebo in a separate window.
Use **Enable driving → Start autonomy → Search for person & return**.
The pink disk is the synthetic person location; the green disk marks the
world's spawn. Home is saved from the robot's actual SLAM pose when Search is
clicked, so a mission started elsewhere returns there instead.

The robot chooses reachable, unvisited viewpoints from its evolving SLAM map,
with 0.38 m map clearance. It receives no target location for planning. A
separate synthetic detector reports the target only within 0.9 m with a clear,
known-free map ray. This is a 360-degree proximity simulation, not person
recognition or camera validation. The dashboard's replay camera remains a
separate demonstration and may be stale.

Finding the marker produces a persistent **SIMULATION** notification, cancels
the exploration goal, waits for cancellation, and navigates to the saved home
position and heading. Completion disarms. Stop, Space, manual takeover,
browser loss, or source expiry cancels every mission phase and never resumes
it. Start a new mission explicitly after interruption. A fresh mission clears
the previous notification and saves a new home.

Search viewpoint selection is bounded to 4 m per axis around home, 48 destinations, and 10 minutes
of wall-clock time. Known unreachable viewpoints are skipped; three consecutive
failed destinations or exhausted coverage triggers a return without claiming a
detection. A search leg has a 90-second deadline, return has 240 seconds, and
failed cancellation, stale map/pose, or failed return stops/disarms with a
failure message. Sudden localization jumps also stop the mission; restart the
simulation before searching again. SLAM's loop search is restricted to nearby
poses for this small, repetitive layout. This demo is map coverage, not a guarantee of complete search
in arbitrary buildings. Narrow passages and moving obstacles need separate
acceptance. Viewpoints must be connected through known free cells, but Nav2
retains its existing route settings and may plan through unknown cells as new
scans arrive; the Collision Monitor remains in the velocity path.

For end-to-end acceptance, close simulation control tabs first:

```bash
docker compose -f ros_ws/docker/compose.yaml restart sim
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_search.py
```

The validator checks Stop/restart, SLAM error against Gazebo truth, a found notification within physical sensor
range and without an obstacle crossing, actual Gazebo clearance and forward
travel, return position/heading, automatic disarming, and a new explicit start.
It always sends Stop. Restore the original world using
`RESCUEBOT_WORLD=indoor_maze.sdf docker compose -f ros_ws/docker/compose.yaml up -d`.

The healthcheck tests dashboard HTTP availability only. It does not establish
working Gazebo sensors, ROS bridges, SLAM, or navigation. Software rendering
works on this Mac. The handoff records validated routes and search results;
different target locations, blocked returns, and physical geometry still need
acceptance. The short
replay fixture expires quickly, so a stale camera banner is expected and does
not indicate a motor or ROS failure.
