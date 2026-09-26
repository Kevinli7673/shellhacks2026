"""Search-only arrival policy, bounded prefetch, and sensing provenance."""

from concurrent.futures import Future
import importlib.util
import math
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import pytest

from rescuebot_navigation.mission_manager import SearchPreparation, prepare_search
from rescuebot_navigation.search import Coverage, Grid
from test_mission_manager import manager, status, accepted_handle
from test_search import grid, start


def preparation(node, pose, point=(1., 0.), *, projected=False, source_grid=None):
    return SearchPreparation(node._coverage, source_grid or node._grid,
                             (pose["x"], pose["y"], pose["yaw"]), point, True, projected)


def test_search_arrival_uses_own_tree_and_home_keeps_original_heading(manager):
    pose = start(manager, {"x": 0., "y": 0., "yaw": 1.})
    manager._action.send_goal_async.return_value = Future()
    manager._search_destination(.8, .2, -2., time.monotonic())
    search_goal = manager._action.send_goal_async.call_args.args[0]
    assert search_goal.behavior_tree.endswith("/behavior_trees/search_viewpoint.xml")
    manager._pending = False
    manager._search["phase"] = "returning"
    manager._search_destination(pose["x"], pose["y"], pose["yaw"], time.monotonic())
    home_goal = manager._action.send_goal_async.call_args.args[0]
    assert home_goal.behavior_tree == ""
    assert home_goal.pose.pose.orientation.z == pytest.approx(math.sin(.5))


def test_ready_prefetch_sends_next_goal_on_first_tick_after_terminal_result(manager):
    pose = start(manager)
    response = Future()
    manager._action.send_goal_async.return_value = response
    manager._search_destination(0., 0., math.pi, time.monotonic())
    handle = accepted_handle()
    response.set_result(handle)
    manager._prefetched = preparation(manager, pose, projected=True)
    manager._last_observation = (manager._grid, (pose["x"], pose["y"]))
    handle.get_result_async.return_value.set_result(SimpleNamespace(status=4))
    manager._planner.submit = Mock(side_effect=AssertionError("must use prepared destination"))
    manager._action.send_goal_async.return_value = Future()
    manager._search_tick(time.monotonic(), pose)
    assert manager._action.send_goal_async.call_count == 2
    assert manager._viewpoint == (1., 0.)
    assert manager._pending


def test_prefetch_runs_while_current_action_is_executing(manager):
    pose = start(manager)
    manager._viewpoint = (.8, .3)
    manager._goal_handle = accepted_handle()
    manager._leg_started = time.monotonic()
    worker = Future()
    manager._planner.submit = Mock(return_value=worker)
    manager._search_tick(time.monotonic(), pose)
    args, kwargs = manager._planner.submit.call_args
    assert args[0] is prepare_search
    assert args[2][:2] == manager._viewpoint
    assert kwargs["projected_view"] == manager._viewpoint
    assert args[4][0][0] is manager._grid
    manager._action.send_goal_async.assert_not_called()


def test_map_changed_after_prefetch_requires_worker_validation(manager):
    pose = start(manager)
    old = manager._grid
    manager._prefetched = preparation(manager, pose)
    manager._grid = Grid(old.width, old.height, old.resolution, old.x, old.y,
                         (100,) + old.data[1:])
    manager._planner.submit = Mock(return_value=Future())
    manager._search_tick(time.monotonic(), pose)
    manager._action.send_goal_async.assert_not_called()
    args, kwargs = manager._planner.submit.call_args
    assert args[1] is manager._grid
    assert kwargs["candidate"] == (1., 0.)
    assert kwargs["projected_view"] is None


def test_far_arrival_does_not_use_projected_destination(manager):
    pose = start(manager)
    projected_pose = {**pose, "x": 1.}
    manager._prefetched = preparation(manager, projected_pose, point=(2., 0.), projected=True)
    manager._planner.submit = Mock(return_value=Future())
    manager._search_tick(time.monotonic(), pose)
    manager._action.send_goal_async.assert_not_called()
    manager._planner.submit.assert_called_once()


def test_predicted_exhaustion_is_checked_again_from_actual_arrival(manager):
    pose = start(manager)
    manager._prefetched = preparation(manager, pose, point=None, projected=True)
    manager._planner.submit = Mock(return_value=Future())
    manager._search_tick(time.monotonic(), pose)
    assert manager._search["phase"] == "exploring"
    assert manager._planner.submit.call_args.kwargs["projected_view"] is None


def test_repeated_stop_start_keeps_only_one_running_worker(manager):
    pose = start(manager)
    worker = Future()
    worker.set_running_or_notify_cancel()
    manager._planner.submit = Mock(return_value=worker)
    manager._search_tick(time.monotonic(), pose)
    for number in range(5):
        status(manager, False, None)
        status(manager, True, f"restart-{number}")
        manager._start_search(time.monotonic(), pose)
        manager._search_tick(time.monotonic(), pose)
    manager._planner.submit.assert_called_once()
    old_coverage = Coverage(frozenset({(100, 100)}))
    worker.set_result(SearchPreparation(old_coverage, manager._grid, (0., 0., 0.), (1., 1.), True, False))
    assert manager._coverage == Coverage()
    manager._planner.submit.return_value = Future()
    manager._search_tick(time.monotonic(), pose)
    assert manager._planner.submit.call_count == 2
    assert manager._coverage == Coverage()
    manager._action.send_goal_async.assert_not_called()


def test_late_prefetch_for_previous_goal_cannot_replace_new_goal_successor(manager):
    pose = start(manager)
    old = Future()
    manager._plan = manager._worker = old
    manager._plan_goal_generation = manager._generation
    manager._generation += 1
    old.set_result(preparation(manager, pose, projected=True))
    manager._collect_preparation()
    assert manager._prefetched is None


def test_observation_backlog_is_bounded_and_keeps_capture_time_maps(manager):
    start(manager)
    old = manager._grid
    manager._record_observation((0., 0.))
    manager._grid = grid(True)
    manager._record_observation((.1, 0.))
    assert manager._observations[0][0] is old
    assert manager._observations[-1][0] is manager._grid
    for index in range(100):
        manager._record_observation((index*.11, 0.))
    assert len(manager._observations) == 16
    assert manager._observations[-1][1] == (99*.11, 0.)


def test_new_map_cannot_retroactively_expand_an_old_observation():
    old = Grid(40, 40, .05, -1., -1., (-1,)*1600)
    new = Grid(40, 40, .05, -1., -1., (0,)*1600)
    result = prepare_search(new, (0., 0., 0.), Coverage(), ((old, (0., 0.)),), (), (), planned=False)
    assert result.coverage == Coverage()


def test_projection_never_becomes_actual_coverage():
    g = grid()
    with patch("rescuebot_navigation.mission_manager.next_viewpoint", return_value=(1., 1.)) as planner:
        result = prepare_search(g, (0., 0., 0.), Coverage(), (), (), (), projected_view=(.8, 0.))
    assert result.coverage == Coverage()
    assert planner.call_args.kwargs["projected_view"] == (.8, 0.)


def test_invalid_candidate_replans_on_current_map_in_worker():
    g = grid()
    with patch("rescuebot_navigation.mission_manager.is_valid_viewpoint", return_value=False) as validate, \
            patch("rescuebot_navigation.mission_manager.next_viewpoint", return_value=(-1., 0.)) as planner:
        result = prepare_search(g, (0., 0., 0.), Coverage(), (), (), (), candidate=(1., 0.))
    assert result.point == (-1., 0.)
    assert validate.call_args.args[0] is g
    planner.assert_called_once()


def test_search_only_controller_derives_unchanged_caps_and_collision_settings():
    package = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("navigation_launch", package / "launch/navigation.launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    nodes = []
    with patch.object(module, "get_package_share_directory", return_value=str(package)), \
            patch.object(module, "Node", side_effect=lambda **kwargs: nodes.append(kwargs) or Mock()):
        module.generate_launch_description()
    controller = next(node for node in nodes if node.get("name") == "controller_server")
    normal = module.yaml.safe_load((package / "config/nav2.yaml").read_text())["controller_server"]["ros__parameters"]
    overrides = controller["parameters"][-1]
    search = overrides["SearchPath"]
    expected = module.deepcopy(normal["FollowPath"])
    expected["rotate_to_goal_heading"] = False
    expected["critics"].remove("RotateToGoal")
    assert search == expected
    assert overrides["search_goal_checker"]["plugin"] == "nav2_controller::PositionGoalChecker"
    assert overrides["search_goal_checker"]["xy_goal_tolerance"] == normal["goal_checker"]["xy_goal_tolerance"]
    search_tree = ET.parse(package / "behavior_trees/search_viewpoint.xml")
    ordinary_tree = ET.parse(package / "behavior_trees/navigate_to_pose.xml")
    assert search_tree.find(".//FollowPath").attrib["goal_checker_id"] == "search_goal_checker"
    assert search_tree.find(".//FollowPath").attrib["controller_id"] == "SearchPath"
    assert ordinary_tree.find(".//FollowPath").attrib["goal_checker_id"] == "goal_checker"


def test_captured_inflight_observation_blocks_old_prefetch_handoff(manager):
    pose = start(manager)
    manager._prefetched = preparation(manager, pose, projected=True)
    manager._last_observation = (manager._grid, (0., 0.))
    manager._observations.append((manager._grid, (1., 0.)))
    manager._planner.submit = Mock(return_value=Future())
    manager._viewpoint = (0., 0.)
    manager._prepare_next(time.monotonic(), pose, moving=True)
    assert not manager._observations and manager._plan is not None
    manager._goal_state = "succeeded"
    manager._search_tick(time.monotonic(), pose)
    manager._action.send_goal_async.assert_not_called()


def test_map_probability_changes_reuse_geometry_but_class_changes_invalidate(manager):
    from nav_msgs.msg import OccupancyGrid
    message = OccupancyGrid()
    message.header.frame_id = "map"
    message.info.width = 3
    message.info.height = 1
    message.info.resolution = .05
    message.info.origin.orientation.w = 1.
    message.data = [0, 100, -1]
    manager._map(message)
    first = manager._grid
    message.data = [49, 50, -1]
    manager._map(message)
    assert manager._grid is first
    message.data = [50, 50, -1]
    manager._map(message)
    assert manager._grid is not first
