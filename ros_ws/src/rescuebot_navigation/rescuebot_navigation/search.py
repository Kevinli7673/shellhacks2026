"""Bounded, sensor-aware search for the simulation; no target coordinates here.

Nav2 owns paths and collision avoidance. This module proposes reachable places
that expose unsearched space, using the simulated 360-degree, 0.9 m detector.
Mapping a cell does not mean that the person detector has searched it.
"""

from collections import deque
from dataclasses import dataclass
import math


SENSOR_RANGE = .9
COVERAGE_RESOLUTION = .1
MAX_COVERAGE_CELLS = 65536
MIN_NEW_CELLS = 12  # 0.12 m² of sampled target-sensing area; skip edge slivers.


@dataclass(frozen=True)
class Grid:
    width: int
    height: int
    resolution: float
    x: float
    y: float
    data: tuple

    def free(self, x, y):
        col, row = math.floor((x-self.x)/self.resolution), math.floor((y-self.y)/self.resolution)
        return (0 <= col < self.width and 0 <= row < self.height
                and 0 <= self.data[row*self.width+col] < 50)

    def visible(self, a, b):
        steps = max(1, math.ceil(math.dist(a, b)/(self.resolution*.5)))
        return all(self.free(a[0]+(b[0]-a[0])*i/steps, a[1]+(b[1]-a[1])*i/steps)
                   for i in range(steps+1))


@dataclass(frozen=True)
class Coverage:
    """Actually sensed world-lattice samples, independent of SLAM grid indices.

    Each key represents a 10 cm cell's centre, not a claim that the complete
    cell or an entire room has been searched. Immutable snapshots can safely
    cross the mission manager's single-worker boundary. The mission resets
    these samples on every new search, even when it reuses the same map.
    """

    cells: frozenset = frozenset()


def _sample(key):
    return ((key[0]+.5)*COVERAGE_RESOLUTION, (key[1]+.5)*COVERAGE_RESOLUTION)


def _nearby(position):
    step = COVERAGE_RESOLUTION
    for x in range(math.ceil((position[0]-SENSOR_RANGE)/step-.5),
                   math.floor((position[0]+SENSOR_RANGE)/step-.5)+1):
        for y in range(math.ceil((position[1]-SENSOR_RANGE)/step-.5),
                       math.floor((position[1]+SENSOR_RANGE)/step-.5)+1):
            key = (x, y)
            p = _sample(key)
            if math.dist(position[:2], p) <= SENSOR_RANGE:
                yield key, p


def observe(grid, position, coverage):
    """Add sensing-time evidence; callers must pass the captured map and pose.

    Never replay old robot positions against a newer map: newly revealed cells
    must remain unsearched until a real observation sees them. Work is bounded
    to the detector disk and existing snapshots are never modified.
    """
    fresh = {key for key, p in _nearby(position)
             if key not in coverage.cells and grid.visible(position[:2], p)}
    room = max(0, MAX_COVERAGE_CELLS-len(coverage.cells))
    # A normal 8 m window uses about 6400 cells. Retain old evidence if an
    # exceptional sequence of map shifts reaches the memory limit.
    return Coverage(coverage.cells | frozenset(sorted(fresh)[:room]))


def _clearance_offsets(grid, radius):
    reach = math.ceil(radius/grid.resolution + .5)
    offsets = [y*grid.width+x
               for x in range(-reach, reach+1)
               for y in range(-reach, reach+1)
               if math.hypot(max(0, abs(x)-.5)*grid.resolution,
                             max(0, abs(y)-.5)*grid.resolution) <= radius]
    return reach, offsets


def _reachable(grid, pose):
    """Full SLAM-cell graph, retaining the accepted narrow-passage geometry."""
    step = grid.resolution
    reach, offsets = _clearance_offsets(grid, .38)
    columns = range(max(reach, math.ceil(grid.width/2-4/step-.5)),
                    min(grid.width-reach, math.floor(grid.width/2+4/step-.5)+1))
    rows = range(max(reach, math.ceil(grid.height/2-4/step-.5)),
                 min(grid.height-reach, math.floor(grid.height/2+4/step-.5)+1))
    points = {}
    for x in columns:
        for y in rows:
            index = y*grid.width+x
            if all(0 <= grid.data[index+offset] < 50 for offset in offsets):
                points[(x, y)] = (grid.x+(x+.5)*step, grid.y+(y+.5)*step)
    if not points:
        return {}, {}
    start = min(points, key=lambda k: math.dist(points[k], pose[:2]))
    if math.dist(points[start], pose[:2]) > .4:
        return {}, {}
    distance = {start: 0.}
    pending = deque([start])
    while pending:
        x, y = pending.popleft()
        for key in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if key in points and key not in distance:
                distance[key] = distance[(x, y)]+step
                pending.append(key)
    return points, distance


def _arrival_clear(grid, key, reach, offsets):
    x, y = key
    return (reach <= x < grid.width-reach and reach <= y < grid.height-reach
            and all(0 <= grid.data[y*grid.width+x+offset] < 50 for offset in offsets))


def _novel(grid, point, coverage):
    return [p for key, p in _nearby(point)
            if key not in coverage.cells and grid.free(*p)]


def _frontiers(grid):
    """Known-free boundary samples; unknown space is never counted as sensed."""
    representatives = {}
    for y in range(grid.height):
        for x in range(grid.width):
            if not 0 <= grid.data[y*grid.width+x] < 50:
                continue
            if any(not (0 <= xx < grid.width and 0 <= yy < grid.height)
                   or grid.data[yy*grid.width+xx] < 0
                   for xx, yy in ((x-1, y), (x+1, y), (x, y-1), (x, y+1))):
                p = (grid.x+(x+.5)*grid.resolution, grid.y+(y+.5)*grid.resolution)
                representatives[(math.floor(p[0]/COVERAGE_RESOLUTION),
                                 math.floor(p[1]/COVERAGE_RESOLUTION))] = p
    return tuple(representatives.values())


def _frontier_gain(grid, point, visited, frontiers):
    # Once known target-sensing area is exhausted, a new view near an unknown
    # boundary can reveal a route. Do not repeatedly approach an unchanged
    # frontier from an already visited, mutually visible location.
    if any(math.dist(point, q) < .70 and grid.visible(point, q) for q in visited):
        return 0
    return sum(math.dist(point, q) <= SENSOR_RANGE and grid.visible(point, q) for q in frontiers)


def _travel_cost(pose, point, distance):
    bearing = math.atan2(point[1]-pose[1], point[0]-pose[0])
    turn = abs(math.atan2(math.sin(bearing-pose[2]), math.cos(bearing-pose[2])))
    # The fixed distance-equivalent overhead discourages tiny gains at many
    # closely spaced goals. The heading penalty preserves forward preference.
    return .6 + distance + .2*turn


def next_viewpoint(grid, pose, visited, rejected, coverage=None, projected_view=None):
    """Maximize newly sensed area per travel cost inside the 8 m map window.

    ``coverage`` contains actual observations. ``projected_view`` is only an
    ephemeral prediction when preparing the goal after an active destination;
    it never changes the caller's actual coverage. Unknown space blocks the
    connectivity graph. Candidate reduction does not coarsen that graph.
    """
    coverage = coverage if coverage is not None else Coverage()
    if projected_view is not None:
        coverage = observe(grid, projected_view, coverage)
    points, distance = _reachable(grid, pose)
    arrival_reach, arrival_offsets = _clearance_offsets(grid, .55)
    buckets = {}
    for key, path_distance in distance.items():
        p = points[key]
        if math.dist(p, pose[:2]) < .65 or any(math.dist(p, q) < .6 for q in rejected):
            continue
        if not _arrival_clear(grid, key, arrival_reach, arrival_offsets):
            continue
        # A representative safe cell in each 25 cm world bucket bounds costly
        # visibility scoring. Even a doorway only one map cell wide remains
        # connected; candidates still require the larger arrival clearance.
        bucket = (math.floor(p[0]/.25), math.floor(p[1]/.25))
        centre = ((bucket[0]+.5)*.25, (bucket[1]+.5)*.25)
        choice = (math.dist(p, centre), p, path_distance)
        if bucket not in buckets or choice < buckets[bucket]:
            buckets[bucket] = choice
    ranked = []
    for _, p, path_distance in buckets.values():
        novel = _novel(grid, p, coverage)
        cost = _travel_cost(pose, p, path_distance)
        ranked.append((len(novel)/cost, p, cost, novel))
    best = None
    # Optimistic gain ignores occlusion; after exact scoring beats the next
    # optimistic bound, additional ray casts cannot improve the result.
    for bound, p, cost, novel in sorted(ranked, key=lambda item: (-item[0], item[1])):
        if best is not None and bound < best[0]:
            break
        gain = sum(grid.visible(p, q) for q in novel)
        if gain >= MIN_NEW_CELLS:
            choice = (gain/cost, gain, -cost, p)
            if best is None or choice > best:
                best = choice
    if best is not None:
        return best[-1]
    frontiers = _frontiers(grid) if ranked else ()
    frontier_choices = []
    for _, p, cost, _ in ranked:
        gain = _frontier_gain(grid, p, visited, frontiers)
        if gain:
            frontier_choices.append((gain/cost, gain, -cost, p))
    return max(frontier_choices)[-1] if frontier_choices else None


def is_valid_viewpoint(grid, pose, point, rejected=(), coverage=None, visited=()):
    """Revalidate a prefetched destination against the latest map and evidence.

    This runs on the planner worker, not the heartbeat. Optional coverage also
    rejects a destination whose sensing footprint has already been searched
    en route, unless it still offers an unvisited frontier view.
    """
    points, distance = _reachable(grid, pose)
    key = (math.floor((point[0]-grid.x)/grid.resolution),
           math.floor((point[1]-grid.y)/grid.resolution))
    if key not in distance:
        return False
    offset = math.dist(point, points[key])
    # A shifted map origin can put a saved world goal off the new cell centre.
    # Expand its clearance disk so the actual saved point retains 0.55 m.
    reach, offsets = _clearance_offsets(grid, .55 + offset)
    if (offset > grid.resolution*.71
            or any(math.dist(point, q) < .6 for q in rejected)
            or not _arrival_clear(grid, key, reach, offsets)):
        return False
    if coverage is None:
        return True
    if sum(grid.visible(point, p) for p in _novel(grid, point, coverage)) >= MIN_NEW_CELLS:
        return True
    return bool(_frontier_gain(grid, point, visited, _frontiers(grid)))
