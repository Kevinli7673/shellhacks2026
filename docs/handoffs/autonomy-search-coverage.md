# Search coverage and scoring handoff

Workstream: simulation search coverage/scoring (#2 and #3)
Branch: feature/autonomy-search-coverage
Worktree: /private/tmp/rescuebot-search-coverage
Base: 877a1b5 (feature/autonomy-sim)
Status: pure implementation/tests ready for sequential integration; live simulator validation belongs to the coordinating agent.

The user explicitly authorized #1–#4 in parallel subagents. This workstream
owns search.py and planner tests. The movement workstream owns the manager,
Nav2 configuration, behavior trees, and manager tests. Interfaces were agreed
before editing. The existing simulation-only scope exception applies; shared
plan/status documents still defer autonomy and need integrator reconciliation.
Fetched origin; no cross-workstream request channel routes to simulation.
No physical motors, firmware, app protocol, speed, or safety limits changed.

## Behavior and interface

- `Coverage()` is an immutable snapshot of actually sensed 10 cm world-lattice
  sample centres, independent of changing SLAM map origin/resolution. Samples
  represent discrete locations, not proof that every point in a cell was seen.
- `observe(grid, position, coverage)` requires the captured sensing-time map
  and pose. It adds only points within the existing 360-degree 0.9 m detector
  range and known-free line of sight. A later map cannot retroactively mark
  old positions' surroundings searched. Memory is capped at 65,536 samples;
  a normal 8 m search window needs about 6,400. Reaching the cap retains old
  evidence and stops adding new samples (safe, conservative undercount).
- `next_viewpoint(grid, pose, visited, rejected, coverage=None,
  projected_view=None)` ranks newly visible area divided by travel cost.
  Cost uses connected map-cell travel distance, a 0.6 m per-goal overhead,
  and the existing 0.2 m/rad heading penalty. At least 12 new samples
  (approximately 0.12 m², 5% of the detector footprint) are needed to avoid
  repeated goals for tiny residual edge slivers. No target coordinates enter
  selection. Optional projected arrival sensing is temporary, never persisted.
- The 8 m map-centred window, full map-resolution connectivity, 0.38 m transit
  clearance, 0.55 m destination clearance, and 0.6 m rejected-goal exclusion
  remain. Safe candidates are reduced to one per 25 cm world bucket for
  scoring, without coarsening transit connectivity. Optimistic gain bounds
  avoid unnecessary exact visibility checks.
- When useful target-coverage gain is exhausted, a reachable new view near
  an unknown boundary may reveal more map. This fallback does not count
  unknown cells as searched and excludes goals near mutually visible visited
  positions, preventing unchanged frontier retries.
- `is_valid_viewpoint(grid, pose, point, rejected=(), coverage=None,
  visited=())` rechecks reachability, clearance and (when supplied) useful
  remaining coverage/frontier gain. A shifted map origin expands the cell
  clearance check to keep the saved world point's margin conservative.
- Movement integration must accumulate observations on its single worker,
  reset coverage for each mission, retain actual vs projected evidence
  separately, and validate prefetched goals against current actual coverage.
  Exhaustion should say “No more useful reachable viewpoints; target not
  found”: sampled coverage/minimum gain is not exhaustive proof of absence.

## Validation

Environment: macOS ARM64 host Python through the existing repository venv.
The venv has no pytest; an attempted `python -m pytest` failed before test
collection. No dependency was installed. All pure test functions were instead
executed directly with normal assertions (no ROS or simulator process).

```bash
PYTHONPATH=ros_ws/src/rescuebot_navigation /Users/shaderahman/Documents/coding/shellhacks2026/.venv/bin/python - <<'PY'
import runpy
module = runpy.run_path('ros_ws/src/rescuebot_navigation/test/test_search_coverage.py')
for name, test in module.items():
    if name.startswith('test_'):
        test()
        print('PASS', name)
PY
git diff --check
```

All 14 new pure tests pass (~2.06 seconds together): detector range, wall and
unknown occlusion, immutable/provisional state, no retroactive map sensing,
origin/resolution changes, motion/idempotence, stale-map/rejected-goal/arrival
revalidation, gain/cost tradeoff, useful revisits, tiny-gain exhaustion,
frontier exhaustion, bounded no-target room search, and memory cap.

The five existing pure planning test functions in test_search.py also passed
via an AST-selected direct runner (six cases, doorway open/unknown are both
executed). They preserve target occlusion, unvisited reachable goals, rejected
goal spacing, unknown-grid exhaustion, map-cell doorway connectivity, safe
arrival beyond the doorway, and northern-start southern-detour regressions.
They now provide genuine sensed coverage instead of inferring search coverage
from visited robot positions. Manager tests were not modified or run here.

An open 6×6 m, 5 cm map observation took ~1.5 ms and initial selection ~276 ms
on this host; this is a single timing sample, not a guaranteed runtime bound.
All expensive coverage and planning operations must stay off the command timer.
No physical or live simulator acceptance has been performed by this subagent.

## Next action

Cherry-pick this branch's implementation commit into the integrating simulation
branch, alongside the movement agent's manager changes. Run all ROS tests and
fresh/northern, varied-target, no-target, obstacle, Stop/takeover/source-loss
simulation acceptance. Evaluate time-to-first-detection in simulation seconds
and distance rather than drawing conclusions from wall time at variable load.
No push is authorized for this subsidiary branch; the coordinator owns final
feature/autonomy-sim publication and the combined handoff.

## Implementation checkpoint and additional reproducibility

Implementation commit: `939e746` (unpublished subsidiary branch). The
coordinator retained the 12-sample minimum pending live varied/absent-target
validation; this is an explicit sampled-coverage limitation, not proof of
complete absence. The following exact command ran the six existing pure
planner cases without importing ROS or installing pytest:

```bash
PYTHONPATH=ros_ws/src/rescuebot_navigation /Users/shaderahman/Documents/coding/shellhacks2026/.venv/bin/python - <<'PY'
import ast
import math
from rescuebot_navigation.search import Coverage, Grid, observe, next_viewpoint
path = 'ros_ws/src/rescuebot_navigation/test/test_search.py'
names = {
    'grid', 'sensed',
    'test_unknown_and_occupied_cells_occlude_target',
    'test_viewpoints_are_reachable_unvisited_and_have_clearance',
    'test_empty_or_unknown_area_has_no_goal',
    'test_offset_passage_connects_rooms_only_when_observed_free',
    'test_north_start_does_not_crop_the_southern_detour',
}
module = ast.parse(open(path).read())
module.body = [node for node in module.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
for node in module.body:
    node.decorator_list = []
namespace = globals()
exec(compile(module, path, 'exec'), namespace)
for name in sorted(names):
    if name.startswith('test_'):
        if name.startswith('test_offset'):
            namespace[name](True)
            namespace[name](False)
        else:
            namespace[name]()
        print('PASS', name)
PY
```

Additional single-sample timings (same host, open all-free maps; current pose
(0, 0, 0), observe at (0, 0) then select one goal):

| Grid | Resolution | Observation | Selection |
|---|---|---|---|
| 120×120 | 0.05 m | 1.4 ms | 260 ms |
| 180×180 | 0.05 m | 1.5 ms | 684 ms |
| 500×500 (maximum accepted cell count) | 0.025 m | 2.8 ms | 8.612 s |

These are fixture measurements, not latency guarantees. The maximum-size,
minimum-resolution fixture retains the pre-existing expensive map-cell disk
clearance loop; it demonstrates why planning remains off the command timer.
The live simulation uses 0.05 m resolution. No additional caching or
resolution change was introduced as part of these selected optimizations.

```bash
PYTHONPATH=ros_ws/src/rescuebot_navigation /Users/shaderahman/Documents/coding/shellhacks2026/.venv/bin/python - <<'PY'
import time
from rescuebot_navigation.search import Coverage, Grid, observe, next_viewpoint
for width, resolution in ((120, .05), (180, .05), (500, .025)):
    origin = -width * resolution / 2
    grid = Grid(width, width, resolution, origin, origin, (0,) * (width * width))
    started = time.monotonic()
    coverage = observe(grid, (0., 0.), Coverage())
    observation = time.monotonic() - started
    started = time.monotonic()
    point = next_viewpoint(grid, (0., 0., 0.), ((0., 0.),), (), coverage)
    print(width, resolution, observation, time.monotonic() - started, point)
PY
```
