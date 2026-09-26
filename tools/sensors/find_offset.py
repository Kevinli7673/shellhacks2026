#!/usr/bin/env python3
"""Find --lidar-offset by comparing a lidar run without you in front of the AI Camera
to one with you standing about 1 m straight in front of it.

Usage: python3 find_offset.py EMPTY_RUN_FOLDER PERSON_RUN_FOLDER
"""
import json, math, statistics, sys

BIN = 2  # degrees per bin


def profile(run):
    """Median distance (mm) in each 2-degree slice, over every scan in the run."""
    bins = {}
    with open(run.rstrip("/") + "/lidar.jsonl") as f:
        for line in f:
            for a, d in json.loads(line)["points"]:
                bins.setdefault(int(a // BIN) * BIN, []).append(d)
    return {b: statistics.median(v) for b, v in bins.items()}


empty, person = profile(sys.argv[1]), profile(sys.argv[2])
closer = sorted(((empty[b] - person[b], b) for b in person if b in empty), reverse=True)

print("Slices that got closest when you stepped in:")
for diff, b in closer[:10]:
    print(f"  {b:3d}-{b + BIN:3d} deg: {empty[b] / 1000:5.2f} m -> {person[b] / 1000:5.2f} m ({diff / 1000:.2f} m closer)")

hits = [b + BIN / 2 for diff, b in closer if diff > 300]  # changed by more than 30 cm
if not hits:
    sys.exit("\nNothing got more than 30 cm closer. Stand closer to the camera and record both runs again.")
x = sum(math.cos(math.radians(a)) for a in hits)
y = sum(math.sin(math.radians(a)) for a in hits)
center = math.degrees(math.atan2(y, x))  # average angle, handling the 0/360 wrap
print(f"\nYou were at about {center % 360:.0f} deg on the lidar ({len(hits)} slices changed).")
print(f"Use: python3 rescue_sensors.py --lidar-offset {center:.0f}")
