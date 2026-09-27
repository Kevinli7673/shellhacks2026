# Robot ROS 2 mapping and autonomy (physical robot)

Mapping (`run_mapping.sh`) builds a SLAM map of the real room while an
operator drives manually from the dashboard; it cannot drive the motors.
Autonomy (`run_autonomy.sh`) adds Nav2 and can drive the real robot, but only
when the dashboard was started with `--allow-physical-autonomy` (off by
default) and the operator has enabled driving and clicked Start autonomy.

The Pi runs Debian 13, which has no ROS 2 packages, so ROS 2 Jazzy runs in a
slim Docker image (`ros:jazzy-ros-base` + `slam_toolbox`, no Gazebo/RViz).

## How it works

- `rescuebot_robot/dashboard_bridge` polls the running dashboard's HTTP API:
  `/api/lidar` (the RPLIDAR C1 bins from `rescuebot.lidar_scan`) becomes
  `/scan`, and `/api/state` `motor.imu.heading` (BNO055) becomes the yaw of
  `odom -> base_link`. It never takes the LiDAR's serial port from the
  dashboard and never opens the control WebSocket or bridge sockets.
- The robot has no wheel encoders, so odometry has no x/y. slam_toolbox adds
  a scan every 0.4 s and its scan matcher finds the motion. Drive slowly.
- `rescuebot_robot/map_viewer` serves the live map at `http://<pi>:8090/`.

## Use

Start the dashboard as usual (it must run with `--sensors live`), then:

    ros_ws/docker/robot/run_mapping.sh            # build + start (use --no-imu to ignore the BNO055)
    docker logs -f rescuebot-mapping
    ros_ws/docker/robot/stop_mapping.sh --save room1   # keep maps/room1.posegraph/.data
    ros_ws/docker/robot/stop_mapping.sh                # or just stop

## Autonomy (drives the real robot)

The dashboard keeps every manual safeguard on top of ROS: it owns arming, the
wheel mix and the bridge; W/A/S/D or Stop cancel autonomy at once; a firmware
disarm, a stale bridge, or 250 ms without a fresh Collision Monitor command
stops and disarms. The container mounts only `/tmp/rescuebot-UID/ros` (the
autonomy and navigation sockets), never the bridge socket or a serial port.

    .venv/bin/rescuebot-dashboard --motor-backend bridge --camera-backend live \
        --sensors live --allow-physical-autonomy [--autonomy-speed 20]
    ros_ws/docker/robot/run_autonomy.sh     # replaces rescuebot-mapping
    docker logs -f rescuebot-autonomy
    docker rm -f rescuebot-autonomy         # stop

In the dashboard: Enable driving, Start autonomy, then Send goal (0.1-2 m) or
Search for person & return. A search ends when the AI Camera sees a person
with a LiDAR range at their bearing, then returns to the start.

`--autonomy-speed` (percent of the PWM ceiling, default 20) is the speed at
Nav2's top velocity (0.08 m/s); raise it if the wheels stall at low PWM.

First test with the chassis raised so the wheels cannot touch the floor:
goals should spin the wheels in the matching direction, a key should take
over, and killing the container should stop the wheels within 250 ms.

## Conventions

Dashboard bins are clockwise from straight ahead; ROS angles are
counterclockwise (REP 103), so the scan is mirrored in `conversions.py`, and
the clockwise BNO055 heading becomes negative yaw. Verify on the first slow
turn: the map must stay sharp while rotating in place.

## Not done yet

Physical acceptance (chassis raised first, then slow floor runs). Without
wheel encoders, SLAM is the only position source; a localization jump stops
a search.
