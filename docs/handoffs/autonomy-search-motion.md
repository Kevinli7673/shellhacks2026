# Search movement and goal handling

Workstream: simulation search movement, user-authorized optimizations 1 and 4.
Branch/worktree: `feature/autonomy-search-motion`, `/private/tmp/rescuebot-search-motion`.
Base: `877a1b5`. Tested movement code: `d92bd3b`.
Status: committed locally, not pushed or integrated. Runtime acceptance is pending with the coordinating simulation agent.
Dependency: coverage API commit `939e7468772da8c06cc23a9dd628bfe67701c96e` from `feature/autonomy-search-coverage`; integrate that commit before this work. Tests combined the coverage source/tests with this movement source in an isolated container directory, without changing installed simulation files.

The user explicitly approved parallel subagents implementing search movement/goal handling and coverage/scoring. This remains the simulation-only scope exception recorded in autonomy-sim.md. No firmware, wheel mixing, serial protocol, dashboard/manual driving policy, collision geometry, velocity/acceleration limits, or watchdog settings changed. No cross-workstream request routing applies here; origin was fetched. Shared plan/current-state sections remain with the integrator.

## Behavior

Intermediate search goals use their own Nav2 behavior tree, a PositionGoalChecker with the existing 0.10 m position tolerance, and a SearchPath controller. SearchPath is derived at launch from the accepted FollowPath configuration, changing only `rotate_to_goal_heading=false` and removing DWB's `RotateToGoal` critic. This avoids both independent sources of final-heading rotation while keeping path-following settings and limits identical. Ordinary dashboard goals and return-home goals retain the original full-pose checker and heading alignment. The ordinary behavior tree now selects its checker explicitly because the controller server has two registered checkers. Both trees preserve the existing replanning and recovery behavior.

The mission manager prepares the following waypoint while the current action is executing. It predicts sensing at that arrival only for candidate scoring. Actual coverage is updated separately from bounded observations containing the pose and immutable map captured at sensing time. The queue holds at most 16 observations; overflow conservatively loses coverage rather than claiming unobserved space was searched. Observations and planning run on one background worker; canceled running work retains its worker slot, so repeated Stop/start cannot create an unbounded executor backlog. Late results from canceled missions cannot update new mission coverage, and late predictions for an earlier goal cannot replace the current goal's successor.

A prepared goal is handed off only after the current action's terminal result, with matching map geometry and actual coverage, no queued/in-flight sensing work, and an arrival within 0.25 m of the planning origin. Changed maps/coverage or unexpected arrival positions force worker validation/replanning. A predicted empty search is always recomputed from actual arrival before ending the search. The map stores free/occupied/unknown classes, so probability changes that do not alter sensing or collision semantics do not repeatedly invalidate work. Cancellation waiting, host/source expiry, manual takeover, and no automatic rearm are preserved. This reduces planning pauses; it does not claim continuous velocity through Nav2 action completion.

Search launch arguments `synthetic_target_x`, `synthetic_target_y`, and `synthetic_target_enabled` default to 1.8, 0.6, and true. They configure only the synthetic detector fixture for varied-target/absent-target acceptance; coordinates are never passed to the planner. The root workstream owns startup environment forwarding. There is no dynamic target mutation API.

## Validation

Environment: macOS host, accepted Docker Ubuntu/ROS Jazzy container; ROS test domain 88 isolated from the running simulator. Physical coverage: none. No runtime source or installed package was changed by this workstream. No build/restart/navigation action was performed by this workstream.

- Combined navigation suite: **54 passed in 6.23 seconds**. Includes previous mission cancellation/source-expiry tests, coverage planner tests, and 15 new movement tests for search-vs-home arrival policy, immediate prepared handoff after action completion, in-motion prefetch, changed-map/position validation, projected-exhaustion recheck, repeated Stop/start worker bounding, late-goal generation protection, capture-time map provenance, projection separation, invalid-candidate replanning, shared controller constants, in-flight sensing handoff blocking, and probability/class map changes.
- `git diff --check`: passed.
- Initial mixed suite used new coverage code with pre-change planner fixtures and had four planner assertion failures; repeating with coverage commit's matching tests passed. No manager/movement test failed in that initial run.

Exact test command, after copying movement sources and coverage commit's search.py/test_search.py/test_search_coverage.py into `/tmp/search-motion-check` in the container:

```bash
/Users/shaderahman/.docker/bin/docker exec -e ROS_DOMAIN_ID=88 rescuebot-autonomy-sim-sim-1 bash -c 'source /opt/ros/jazzy/setup.bash && source /workspace/ros_ws/install/setup.bash && export PYTHONPATH=/tmp/search-motion-check/ros_ws/src/rescuebot_navigation:/tmp/search-motion-check/app:$PYTHONPATH && python3 -m pytest -q /tmp/search-motion-check/ros_ws/src/rescuebot_navigation/test'
git diff --check
```

The package now explicitly declares `ament_index_python` and `python3-yaml`, already present in the tested ROS environment, to resolve installed behavior-tree paths and derive the search controller configuration. Package setup installs the new XML files.

## Next action

The coordinator should integrate coverage and movement sequentially, build the combined simulation image, verify controller/checker loading, then run fresh/northern/varied/absent-target missions and normal-goal, obstacle-stop, Stop/manual takeover/source-loss regressions. Measure simulation time to first detection, path length, and goal-transition delay; no runtime speedup is claimed from unit tests. The immutable last passing baseline remains at `877a1b5`.

Source behavior inspected for the policy split: [Jazzy rotation shim](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_rotation_shim_controller/src/nav2_rotation_shim_controller.cpp), [position checker](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_controller/plugins/position_goal_checker.cpp), and [DWB final-heading critic](https://github.com/ros-navigation/navigation2/blob/jazzy/nav2_dwb_controller/dwb_critics/src/rotate_to_goal.cpp). Installed `/opt/ros/jazzy/share/nav2_controller/plugins.xml` confirms PositionGoalChecker is available; both trees derive from the installed `navigate_to_pose_w_replanning_and_recovery.xml`.
