"""Bounded map coverage for the simulation demo; no target coordinates here.

Choose reachable, unobserved viewpoints from the latest SLAM grid. The
viewpoint connectivity graph excludes unknown space. New viewpoints expose
previously occluded rooms.
Nav2 owns paths and collision avoidance; this module only proposes destinations.
"""

from collections import deque
from dataclasses import dataclass
import math


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


def next_viewpoint(grid, pose, visited, rejected):
    """Cover an 8 m map-centred window, with 0.38 m wall clearance.

    A connected grid at map resolution preserves narrow mapped passages. The
    caller runs this computation off its command/status timer and discards
    results when the mission changes. The window bounds work independently
    of map size. Rejected viewpoints are not retried.
    """
    step, radius = grid.resolution, .38
    # Home is only a return destination. Centring coverage there can cut off
    # an entire detour when a mission starts near a wall. Use the map centre
    # and its own cells instead. A separate 25 cm lattice can disconnect
    # a usable corridor depending on its alignment with the walls.
    reach = math.ceil(radius/step + .5)
    # Include every cell whose square touches the clearance disk, not just
    # cells whose centres lie inside it. Unknown cells also block passage.
    offsets = [y*grid.width+x
               for x in range(-reach, reach+1)
               for y in range(-reach, reach+1)
               if math.hypot(max(0, abs(x)-.5)*step, max(0, abs(y)-.5)*step) <= radius]
    # Waypoints need room to arrive and turn: the stop polygon's corner is
    # 0.397 m from the centre, Nav2 can finish 0.10 m short, and the map has
    # 0.05 m cells. Keep transit connectivity through narrower passages, but
    # place destinations in space with this extra arrival margin.
    arrival_radius = .55
    arrival_reach = math.ceil(arrival_radius/step + .5)
    arrival_offsets = [y*grid.width+x
                       for x in range(-arrival_reach, arrival_reach+1)
                       for y in range(-arrival_reach, arrival_reach+1)
                       if math.hypot(max(0, abs(x)-.5)*step, max(0, abs(y)-.5)*step) <= arrival_radius]
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
        return None
    start = min(points, key=lambda k: math.dist(points[k], pose[:2]))
    if math.dist(points[start], pose[:2]) > .4:
        return None
    distance = {start: 0.}
    pending = deque([start])
    while pending:
        x, y = pending.popleft()
        for key in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if key in points and key not in distance:
                distance[key] = distance[(x, y)]+step
                pending.append(key)
    candidates = []
    for key, path_distance in distance.items():
        p = points[key]
        if math.dist(p, pose[:2]) < .65:
            continue
        if any(math.dist(p, q) < .6 for q in rejected):
            continue
        if any(math.dist(p, q) < .70 and grid.visible(p, q) for q in visited):
            continue
        x, y = key
        if not (arrival_reach <= x < grid.width-arrival_reach
                and arrival_reach <= y < grid.height-arrival_reach):
            continue
        index = y*grid.width+x
        if not all(0 <= grid.data[index+offset] < 50 for offset in arrival_offsets):
            continue
        # Prefer a nearby viewpoint with less turning, without knowing where
        # the target is or assuming that unmapped space is empty.
        bearing = math.atan2(p[1]-pose[1], p[0]-pose[0])
        turn = abs(math.atan2(math.sin(bearing-pose[2]), math.cos(bearing-pose[2])))
        candidates.append((path_distance + .2*turn, p))
    return min(candidates)[1] if candidates else None
