# Frontier exploration evaluation

Milestone C begins only after simulated SLAM, Nav2 goals, Collision Monitor,
and manual override pass. The first candidate is
`mertgulerx/frontier_exploration_ros2`, pinned to the exact tested source
revision in the ROS workspace rather than copied into this repository.

Accept the package only if it meets all of these checks in the Rescuebot world:

1. Builds on Ubuntu 24.04 with ROS 2 Jazzy and Gazebo Harmonic.
2. Uses the existing `/map`, `/scan`, Nav2 `NavigateToPose`, and TF frames.
3. Cancels immediately when `/rescuebot/autonomy_status` becomes inactive.
4. Does not publish directly to `/cmd_vel` or bypass `/cmd_vel_safe`.
5. Blacklists failed goals and reports frontier-exhaustion completion.
6. Completes the indoor maze twice without a collision or stale-command fault.

If any required check fails, implement a small Rescuebot frontier node in
`rescuebot_navigation` that uses the same map, action client, and cancellation
path. Do not introduce another velocity or motor-control route.
