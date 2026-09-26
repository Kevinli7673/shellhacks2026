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


def next_viewpoint(grid, pose, home, visited, rejected):
    """Cover known free space within 4 m of home, with 0.38 m wall clearance.

    A coarse connected grid bounds work independently of SLAM map size. The
    caller runs this computation off its command/status timer and discards
    results when the mission changes. Rejected viewpoints are not retried.
    """
    step, radius = .25, .38
    offsets = [(x*grid.resolution, y*grid.resolution)
               for x in range(-math.ceil(radius/grid.resolution), math.ceil(radius/grid.resolution)+1)
               for y in range(-math.ceil(radius/grid.resolution), math.ceil(radius/grid.resolution)+1)
               if math.hypot(x*grid.resolution, y*grid.resolution) <= radius]
    points = {}
    for x in range(-16, 17):
        for y in range(-16, 17):
            p = (home[0]+x*step, home[1]+y*step)
            if all(grid.free(p[0]+dx, p[1]+dy) for dx, dy in offsets):
                points[(x, y)] = p
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
        # Prefer a nearby viewpoint with less turning, without knowing where
        # the target is or assuming that unmapped space is empty.
        bearing = math.atan2(p[1]-pose[1], p[0]-pose[0])
        turn = abs(math.atan2(math.sin(bearing-pose[2]), math.cos(bearing-pose[2])))
        candidates.append((path_distance + .2*turn, p))
    return min(candidates)[1] if candidates else None
