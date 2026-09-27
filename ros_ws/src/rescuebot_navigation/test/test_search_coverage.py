"""Pure sensor coverage/scoring checks; runnable without a ROS installation."""

import math

from rescuebot_navigation.search import (
    Coverage, Grid, MIN_NEW_CELLS, observe, next_viewpoint, is_valid_viewpoint,
)


def room(width=80, height=80, resolution=.05, x=-2., y=-2., value=None):
    return Grid(width, height, resolution, x, y,
                tuple(value(x+(col+.5)*resolution, y+(row+.5)*resolution)
                      if value else 0 for row in range(height) for col in range(width)))


def all_samples(grid):
    # Synthetic complete evidence for algorithm tests, not runtime sensing.
    return Coverage(frozenset((x, y) for x in range(-40, 40) for y in range(-40, 40)
                             if grid.free((x+.5)*.1, (y+.5)*.1)))


def test_observation_has_range_and_does_not_mutate_snapshot():
    before = Coverage()
    after = observe(room(), (.05, .05), before)
    assert before.cells == frozenset()
    assert (0, 0) in after.cells and (8, 0) in after.cells
    assert (10, 0) not in after.cells
    assert all(math.dist((.05, .05), ((x+.5)*.1, (y+.5)*.1)) <= .9
               for x, y in after.cells)


def test_unknown_and_wall_occlude_observed_area():
    for obstacle in (-1, 100):
        grid = room(value=lambda x, y: obstacle if -.1 < x < .1 else 0)
        coverage = observe(grid, (-.3, .05), Coverage())
        assert (-3, 0) in coverage.cells
        assert (2, 0) not in coverage.cells
        assert (0, 0) not in coverage.cells


def test_map_reveal_is_not_retroactive_sensing():
    hidden = room(value=lambda x, y: -1 if x > .1 else 0)
    captured = observe(hidden, (0., 0.), Coverage())
    revealed = room()
    # Planning may use a revealed map, but cannot rewrite captured evidence.
    original = captured.cells
    next_viewpoint(revealed, (0., 0., 0.), ((0., 0.),), (), captured)
    assert captured.cells is original and (4, 0) not in captured.cells
    later = observe(revealed, (0., 0.), captured)
    assert (4, 0) in later.cells


def test_coverage_world_coordinates_survive_origin_and_resolution_changes():
    original = observe(room(), (.05, .05), Coverage())
    expanded = room(140, 120, .05, -3.55, -3.05)
    resampled = room(160, 160, .025)
    assert observe(expanded, (.05, .05), Coverage()) == original
    assert observe(resampled, (.05, .05), Coverage()) == original
    assert observe(expanded, (.05, .05), original) == original


def test_repeated_observation_is_idempotent_and_motion_adds_coverage():
    grid = room()
    first = observe(grid, (0., 0.), Coverage())
    assert observe(grid, (0., 0.), first) == first
    second = observe(grid, (.2, 0.), first)
    assert second.cells > first.cells


def test_obstacle_map_change_invalidates_prefetched_goal():
    grid = room()
    pose = (-1., 0., 0.)
    goal = (.8, 0.)
    assert is_valid_viewpoint(grid, pose, goal)
    blocked = room(value=lambda x, y: 100 if abs(x) < .15 else 0)
    assert not is_valid_viewpoint(blocked, pose, goal)
    assert not is_valid_viewpoint(grid, pose, goal, rejected=(goal,))
    assert not is_valid_viewpoint(grid, pose, (1.7, 0.))  # no arrival margin


def test_prefetch_is_provisional_and_enroute_sensing_can_remove_its_value():
    grid = room()
    coverage = observe(grid, (0., 0.), Coverage())
    original = coverage.cells
    projected = (.8, 0.)
    point = next_viewpoint(grid, (*projected, 0.), ((0., 0.), projected), (),
                           coverage, projected_view=projected)
    assert point is not None and coverage.cells is original
    assert (14, 0) not in coverage.cells
    full = all_samples(grid)
    assert not is_valid_viewpoint(grid, (*projected, 0.), point,
                                 coverage=full, visited=(point,))


def test_gain_per_travel_cost_prefers_large_nearby_unsearched_area():
    grid = room(120, 120, x=-3., y=-3.)
    complete = all_samples(grid)
    # Equal useful patches on either side: the nearer one wins at equal yaw.
    unseen = {(x, y) for x in range(-25, 26) for y in range(-5, 5)
              if -2.5 < (x+.5)*.1 < -1.5 or .8 < (x+.5)*.1 < 1.8}
    coverage = Coverage(complete.cells - unseen)
    point = next_viewpoint(grid, (0., 0., math.pi/2), ((0., 0.),), (), coverage)
    assert point is not None and point[0] > .3
    # Reduce the nearby patch to a negligible sliver: farther useful area wins.
    tiny = {(x, y) for x, y in unseen if x < 0 or (x == 12 and y == 0)}
    point = next_viewpoint(grid, (0., 0., math.pi/2), ((0., 0.),), (),
                           Coverage(complete.cells-tiny))
    assert point is not None and point[0] < -.3


def test_previously_visited_location_can_offer_newly_revealed_search_area():
    grid = room()
    complete = all_samples(grid)
    unseen = {(x, y) for x in range(4, 14) for y in range(-4, 4)}
    coverage = Coverage(complete.cells-unseen)
    visited = tuple((x*.2, y*.2) for x in range(-8, 9) for y in range(-8, 9))
    point = next_viewpoint(grid, (-1., 0., 0.), visited, (), coverage)
    assert point is not None and point[0] > 0.


def test_zero_or_tiny_gain_exhausts_without_frontier_bouncing():
    # Explicit occupied boundary makes the fully known room unambiguous.
    grid = room(value=lambda x, y: 100 if abs(x) > 1.9 or abs(y) > 1.9 else 0)
    full = all_samples(grid)
    assert next_viewpoint(grid, (0., 0., 0.), (), (), full) is None
    tiny = frozenset(list(sorted(full.cells))[:MIN_NEW_CELLS-1])
    assert next_viewpoint(grid, (0., 0., 0.), (), (), Coverage(full.cells-tiny)) is None


def test_unknown_frontier_can_be_approached_once_without_claiming_it_searched():
    grid = room(value=lambda x, y: -1 if x > 1. else 0)
    full = all_samples(grid)
    pose = (-1., 0., 0.)
    point = next_viewpoint(grid, pose, (pose[:2],), (), full)
    assert point is not None
    assert all(grid.free((x+.5)*.1, (y+.5)*.1) for x, y in full.cells)
    # Coverage exhausted; a dense history of actual visited locations blocks
    # retries at unchanged boundaries. No target or unknown-cell oracle exists.
    visited = tuple((x*.2, y*.2) for x in range(-8, 9) for y in range(-8, 9))
    assert next_viewpoint(grid, pose, visited, (), full) is None


def test_prefetch_rechecks_arrival_clearance_after_origin_shift():
    grid = room()
    assert is_valid_viewpoint(grid, (0., 0., 0.), (1.25, 0.))
    shifted = room(80, 80, .05, -2.02, -2.02)
    assert is_valid_viewpoint(shifted, (0., 0., 0.), (1.25, 0.))


def test_absent_target_room_search_exhausts_with_bounded_distinct_goals():
    grid = room(value=lambda x, y: 100 if abs(x) > 1.9 or abs(y) > 1.9 else 0)
    pose = (0., 0., 0.)
    visited = [(0., 0.)]
    coverage = observe(grid, pose[:2], Coverage())
    goals = []
    for _ in range(48):
        point = next_viewpoint(grid, pose, visited, (), coverage)
        if point is None:
            break
        assert point not in goals
        goals.append(point)
        steps = math.ceil(math.dist(pose[:2], point)/.1)
        for index in range(1, steps+1):
            position = tuple(pose[axis]+(point[axis]-pose[axis])*index/steps for axis in (0, 1))
            coverage = observe(grid, position, coverage)
            visited.append(position)
        pose = (*point, math.atan2(point[1]-pose[1], point[0]-pose[0]))
    assert 2 <= len(goals) < 16
    assert next_viewpoint(grid, pose, visited, (), coverage) is None


def test_coverage_budget_retains_existing_evidence():
    from rescuebot_navigation.search import MAX_COVERAGE_CELLS
    full = Coverage(frozenset((1000, y) for y in range(MAX_COVERAGE_CELLS)))
    assert observe(room(), (0., 0.), full) == full
