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
Blended translation/rotation and a seven-goal route around the divider have
passed ground-truth pose checks. The controller uses a 0.60 m local path
horizon so goals across a wall follow the planned detour instead of stalling.
NavFn uses Dijkstra expansion: A* repeatedly failed to extract the northern
return path from a reachable potential in this house's SLAM costmap.
The Ubuntu suite reports 168 tests (166 pass, 2 environment-gated skips), and
all 27 ROS tests pass. This is simulation coverage only.

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

### Simulation playback speed

In **Simulation autonomy → Simulation playback**, click **Stop**, then choose
**1×**, **2×**, or **3×**. Wait for the requested rate to be confirmed, then
enable driving and start autonomy as usual. The selection lasts until Gazebo
restarts; a restart restores the world's 1× default. Only the controlling
browser can change the rate, and changes are rejected while driving is enabled.

Playback changes Gazebo's world clock. It keeps the 1 ms physics step, motor
velocity limits, Nav2 configuration, and the 20% autonomy setting unchanged.
The separate **Motor speed** / Up-Down controls still adjust manual driving.
Stop and command-expiry deadlines use wall time and are not multiplied by the
playback setting. The short replay camera fixture is not a Gazebo camera and
does not accelerate with the world; watch the Gazebo desktop.

**Actual** reports a one-second average from Gazebo's elapsed clocks. Requested 3× may run below 3×
when CPU or rendering cannot keep up; missing statistics show **Clock
unavailable**. On the tested Apple Silicon Docker setup, 2× and 3× reached
roughly 1.7–2.1× during the motion probes with the obstacle house and navigation
stack running; other portions of the run approached the requested 3×.
The world clock setting follows Gazebo's
[Harmonic physics update implementation](https://github.com/gazebosim/gz-sim/blob/gz-sim8/src/SimulationRunner.cc).

From a fresh stopped world, with the simulation control tab closed:

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_playback.py
# Set the clock without driving, e.g. before search acceptance after a restart:
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_playback.py --set-only 3
```

This checks clock acceleration, distance per simulated second, rejection while
armed, and stationary Stop holds at each setting. It leaves playback at 3× and
driving disarmed for subsequent navigation tests. It requires useful acceleration
on the validation host; a heavily loaded machine may fail the performance check.

### Runtime checks

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
   Nav2 translates and turns together through ordinary bends, using mecanum
   diagonal travel and strafe. Sharp reversals may first turn in place, and
   final heading correction may also be stationary. It finishes facing the bearing from the starting position to
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
second command adds a reversal and a longer route around the divider. Use the
original `indoor_maze.sdf` world and restart before each run so it begins at
spawn. The validator reads continuous actual model poses and checks final
heading, destination error, bounded reverse travel, and wall/divider clearance.
The first two clear turns must translate at least 10 cm while measurably
rotating and move before full alignment. `--baseline` records these metrics
without requiring blending, for comparison with the older controller.
The rotation shim is reserved for heading errors above 1.75 rad (100°) and
hands back to DWB below 0.65 rad (37°). X/Y/yaw limits remain unchanged.

```bash
RESCUEBOT_WORLD=indoor_maze.sdf docker compose -f ros_ws/docker/compose.yaml up -d
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
# From a fresh start: insert during simultaneous translation and rotation.
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_obstacle.py --heading -90 --during-turn
```

The robot travels toward its goal. A red panel or cylinder
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

The robot chooses reachable viewpoints that expose useful unsearched space
for their travel cost, with 0.38 m map clearance. It receives no target location for planning. A
separate synthetic detector reports the target only within 0.9 m with a clear,
known-free map ray. This is a 360-degree proximity simulation, not person
recognition or camera validation. The dashboard's replay camera remains a
separate demonstration and may be stale.

Search coverage records samples actually visible within the detector's range
using the map available at observation time. Newly mapped space is not marked
searched by replaying earlier robot positions. Coverage resets for every new
mission. Candidate selection balances new visible coverage with reachable
travel distance and turning; unknown space remains blocked in its connectivity
graph. Coverage uses 10 cm sample spacing and requires at least 12 new samples
for a coverage goal, avoiding repeated trips for tiny residual gains. This is
an approximate, bounded search; completion without detection does not prove
that a person is absent. A bounded planner worker prepares the next destination while driving,
then checks its map and coverage before using it. Intermediate search goals
finish on position without a final-heading pause. Dashboard goals and return
home still finish with their required orientation. Nav2 may briefly stop at
goal handoff; this is not a promise of uninterrupted motion.

Finding the marker produces a persistent **SIMULATION** notification, cancels
the exploration goal, waits for cancellation, and navigates to the saved home
position and heading. Completion disarms. Stop, Space, manual takeover,
browser loss, or source expiry cancels every mission phase and never resumes
it. Start a new mission explicitly after interruption. A fresh mission clears
the previous notification and saves a new home.

Search viewpoint selection uses an 8 m square centred on the SLAM map and
checks connectivity at the map's cell resolution (5 cm in this demo). Occupied
and unknown cells keep a 0.38 m centre clearance along the connectivity graph.
Destinations require 0.55 m so the robot can arrive and turn: this covers the
stop polygon's 0.397 m corner radius, 0.10 m Nav2 arrival tolerance, and 0.05 m
map resolution. The planner checks each intersecting cell, including its area.
Home remains the position/heading saved when this mission starts; it does not
limit coverage. Starting near a wall therefore does not crop the detour through
the opposite side of this 6 m house. Work remains
bounded to 48 destinations and 10 minutes of wall-clock time.
Known unreachable viewpoints are skipped; three consecutive
failed destinations or exhausted coverage triggers a return without claiming a
detection. A search leg has a 90-second deadline, return has 240 seconds, and
failed cancellation, stale map/pose, or failed return stops/disarms with a
failure message. If no target is found, the completion message retains why the
search ended (no reachable viewpoints, search limit, timeout, or route failures)
after returning home. Sudden localization jumps also stop the mission; restart
the simulation before searching again. SLAM's loop search is restricted to nearby
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

The validator checks Stop/restart, SLAM error against Gazebo truth, a found notification within the synthetic sensor
range and without an obstacle crossing, actual Gazebo clearance and forward
travel, return position/heading, automatic disarming, and a new explicit start.
It reports wall time, simulation time, traveled distance, sampled stationary
and rotation-only intervals, and time/distance to first detection. Compare
simulation time and distance when playback load differs between runs.
To reproduce the northern-start case after a spawn-start test, run:

```bash
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_search.py --north-start
```

This drives through the dashboard to approximately (-0.55, 2.15) m, then checks
the same search, return, and stop sequence. Both variants always send Stop.

Detector-only fixture overrides support acceptance at other target locations
and with no target. These values never reach viewpoint selection. They do not
move or remove the decorative pink disk in Gazebo; it represents the default
fixture only. Close control tabs and leave driving disarmed before recreating
the simulator:

```bash
# A different target in the house's western area.
RESCUEBOT_WORLD=search_house.sdf RESCUEBOT_SYNTHETIC_TARGET_X=-2.2 RESCUEBOT_SYNTHETIC_TARGET_Y=1.7 docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_search.py
# Require a completed return without any detection.
RESCUEBOT_WORLD=search_house.sdf RESCUEBOT_SYNTHETIC_TARGET_ENABLED=false docker compose -f ros_ws/docker/compose.yaml up -d
docker compose -f ros_ws/docker/compose.yaml exec -T sim bash ros_ws/docker/entrypoint.sh python3 ros_ws/docker/validate_search.py --expect-absent
# Restore the default detector fixture and a fresh house.
RESCUEBOT_WORLD=search_house.sdf docker compose -f ros_ws/docker/compose.yaml up -d
```

Restore the original world using
`RESCUEBOT_WORLD=indoor_maze.sdf docker compose -f ros_ws/docker/compose.yaml up -d`.

The healthcheck tests dashboard HTTP availability only. It does not establish
working Gazebo sensors, ROS bridges, SLAM, or navigation. Software rendering
works on this Mac. The handoff records validated routes and search results;
different target locations, blocked returns, and physical geometry still need
acceptance. The short
replay fixture expires quickly, so a stale camera banner is expected and does
not indicate a motor or ROS failure.
