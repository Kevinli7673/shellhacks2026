# Robot ROS 2 mapping (physical robot, read-only)

Step 1 of physical autonomy: build a SLAM map of the real room while an
operator drives manually from the dashboard. Nothing here can drive the
motors; autonomy stays gated to `--motor-backend gazebo`.

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

## Conventions

Dashboard bins are clockwise from straight ahead; ROS angles are
counterclockwise (REP 103), so the scan is mirrored in `conversions.py`, and
the clockwise BNO055 heading becomes negative yaw. Verify on the first slow
turn: the map must stay sharp while rotating in place.

## Not done yet

Real person detection in ROS, Nav2 on the robot, a drive path from Nav2
through the bridge with Stop/manual override on top, and physical acceptance
(chassis raised first, then slow floor runs).
