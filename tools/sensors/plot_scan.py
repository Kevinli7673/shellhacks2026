#!/usr/bin/env python3
"""Plot the last lidar scan from a rescuebot run, with the AI Camera's view shaded green.

Usage: python3 plot_scan.py RUN_FOLDER [LIDAR_OFFSET_DEG]
Saves scan.png in the run folder.
"""
import json, math, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
run, offset = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
scan = json.loads(open(run + "/lidar.jsonl").readlines()[-1])["points"]
ax = plt.subplot(projection="polar")
ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)  # 0 deg up, clockwise like the lidar
ax.scatter([math.radians(a) for a, d in scan], [d / 1000 for a, d in scan], s=3)
rmax = min(max(d for a, d in scan) / 1000, 12)
ax.fill_between([math.radians(offset - 33), math.radians(offset + 33)], 0, rmax, alpha=0.2, color="green")
ax.set_title(f"Last lidar scan (m); green = AI Camera view, offset {offset:g}")
plt.savefig(run + "/scan.png", dpi=120); print("saved", run + "/scan.png")
