"""Map-driven search, synthetic sensing, return, and cancellation boundaries."""

from concurrent.futures import Future
import math
import time
from types import SimpleNamespace

import pytest
from nav_msgs.msg import OccupancyGrid

from rescuebot_navigation.search import Grid, next_viewpoint
from test_mission_manager import manager, status, accepted_handle


def grid(wall=False):
    data = tuple(100 if wall and 39 <= x <= 41 else 0 for y in range(80) for x in range(80))
    return Grid(80, 80, .05, -2, -2, data)


def start(node, pose=None):
    status(node)
    node._grid = grid()
    node._search["available"] = True
    pose = pose or {"x": 0., "y": 0., "yaw": 0.}
    node._start_search(time.monotonic(), pose)
    return pose


def test_unknown_and_occupied_cells_occlude_target():
    g = grid(True)
    assert not g.visible((-1, 0), (1, 0))
    assert g.visible((-1, 0), (-.5, 0))
    unknown = Grid(2, 1, .1, 0, 0, (0, -1))
    assert not unknown.visible((.05, .05), (.15, .05))
    assert not g.free(100, 0)


def test_viewpoints_are_reachable_unvisited_and_have_clearance():
    g = grid(True)
    home = (-1, 0)
    p = next_viewpoint(g, (*home, 0), home, (home,), ())
    assert p is not None and p[0] < -.4
    assert math.dist(p, home) >= .7
    other = next_viewpoint(g, (*home, 0), home, (home,), (p,))
    assert math.dist(other, p) >= .6


def test_empty_or_unknown_area_has_no_goal():
    assert next_viewpoint(Grid(10, 10, .05, 0, 0, (-1,)*100), (0, 0, 0), (0, 0), (), ()) is None


def test_detection_stops_then_returns_only_after_action_terminal_result(manager):
    pose = start(manager, {"x": 1.2, "y": .6, "yaw": .4})
    now = time.monotonic()
    response = Future()
    manager._action.send_goal_async.return_value = response
    manager._search_destination(1.7, .6, 0, now)
    manager._search_tick(now, pose)
    assert manager._search["found"] and manager._search["phase"] == "notifying"
    handle = accepted_handle()
    response.set_result(handle)
    handle.cancel_goal_async.assert_called_once()
    manager._search_tick(now+2, pose)
    manager._action.send_goal_async.assert_called_once()
    handle.get_result_async.return_value.set_result(SimpleNamespace(status=5))
    manager._action.send_goal_async.return_value = Future()
    manager._search_tick(now+2, pose)
    home = manager._action.send_goal_async.call_args.args[0].pose.pose
    assert manager._search["phase"] == "returning"
    assert home.position.x == 1.2 and home.position.y == .6
    assert home.orientation.z == pytest.approx(math.sin(.4/2))


def test_stopping_search_prevents_return_and_new_start_records_new_home(manager):
    pose = start(manager, {"x": 1.2, "y": .6, "yaw": 0.})
    manager._search_tick(time.monotonic(), pose)
    assert manager._search["found"]
    status(manager, False, None)
    manager._search_tick(time.monotonic()+2, pose)
    manager._action.send_goal_async.assert_not_called()
    assert manager._search["phase"] == "canceled"
    status(manager, True, "new")
    manager._start_search(time.monotonic(), {"x": -.5, "y": 0., "yaw": 1.})
    assert manager._search["home"]["x"] == -.5 and not manager._search["found"]


def test_target_behind_wall_does_not_notify(manager):
    pose = start(manager, {"x": -.3, "y": 0., "yaw": 0.})
    manager._grid = grid(True)
    manager._target = (.3, 0.)
    manager._search_tick(time.monotonic(), pose)
    assert not manager._search["found"]
    manager._cancel_search()


def test_search_limit_returns_without_claiming_target(manager):
    pose = start(manager)
    now = manager._search_started + 601
    manager._search_tick(now, pose)
    assert manager._search["phase"] == "return_pending" and not manager._search["found"]
    manager._action.send_goal_async.return_value = Future()
    manager._search_tick(now+2, pose)
    assert manager._search["phase"] == "returning"


def test_return_failure_stops_and_cannot_report_success(manager):
    pose = start(manager)
    manager._search["phase"] = "returning"
    manager._goal_state = "aborted"
    manager._search_tick(time.monotonic(), pose)
    assert manager._search["phase"] == "failed"
    manager._search["phase"] = "returning"
    manager._goal_state = "succeeded"
    manager._search_tick(time.monotonic(), {"x": .5, "y": 0., "yaw": 0.})
    assert manager._search["phase"] == "failed"


def test_cancel_timeout_never_starts_a_return_goal(manager):
    pose = start(manager)
    now = time.monotonic()
    manager._pending = True
    manager._begin_return(now, "test")
    manager._search_tick(now+6, pose)
    assert manager._search["phase"] == "failed"
    manager._action.send_goal_async.assert_not_called()


def test_completed_mission_does_not_disarm_a_later_start(manager):
    start(manager)
    manager._search.update(phase="complete", found=True)
    status(manager, False, None)
    assert manager._search["phase"] == "complete"
    status(manager, True, "new")
    assert manager._search["phase"] == "idle" and not manager._search["found"]


def test_late_planning_result_after_stop_cannot_send_a_goal(manager):
    start(manager)
    old_plan = Future()
    old_plan.set_running_or_notify_cancel()
    manager._plan = old_plan
    status(manager, False, None)
    status(manager, True, "next")
    old_plan.set_result((1., 1.))
    assert manager._plan is None and manager._search["phase"] == "idle"
    manager._action.send_goal_async.assert_not_called()


def test_invalid_maps_do_not_refresh_readiness(manager):
    message = OccupancyGrid()
    message.header.frame_id = "map"
    message.info.width = message.info.height = 1
    message.info.resolution = .05
    message.info.origin.orientation.w = 1.
    message.data = [0]
    manager._map(message)
    first = manager._map_received
    message.info.origin.position.x = float("nan")
    manager._map(message)
    assert manager._map_received == first
    message.info.origin.position.x = 0.
    message.info.width = 1000000
    manager._map(message)
    assert manager._map_received == first


def test_stale_map_stops_search_without_sending_home(manager):
    pose = start(manager)
    manager._search["available"] = False
    manager._search_tick(time.monotonic(), pose)
    assert manager._search["phase"] == "failed"
    manager._action.send_goal_async.assert_not_called()


def test_localization_jump_fails_instead_of_navigating_to_wrong_home(manager):
    pose = start(manager)
    manager._search_tick(time.monotonic(), {**pose, "x": 3.})
    assert manager._search["phase"] == "failed"
    assert "Localization jumped" in manager._search["reason"]
    manager._action.send_goal_async.assert_not_called()
