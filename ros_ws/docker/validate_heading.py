"""Check blended translation/turning against Gazebo ground truth.

Run in a fresh simulation with control tabs closed. Optional --long-routes
continues through the lower opening around the world's divider. No real
backend is accepted. Every exit sends Stop.
"""

import argparse
import asyncio
import json
import math
import time

import websockets

from validate_manual import displacement, pose_stream, state as fetch_state


def angle(value):
    return math.atan2(math.sin(value), math.cos(value))


async def run(pose, long_routes=False, detour=False, baseline=False):
    current = await asyncio.to_thread(fetch_state)
    assert current["motor"]["backend"] == "gazebo"
    assert current["control"]["owner_session"] is None, "close simulation control tabs"
    assert not current["control"]["armed"]
    initial = await pose()
    assert math.hypot(*initial[:2]) < .05 and abs(initial[2]) < .05, "restart the simulation at its spawn"
    received = time.monotonic()

    async def poll():
        nonlocal current, received
        while True:
            current = await asyncio.to_thread(fetch_state)
            received = time.monotonic()
            await asyncio.sleep(.05)

    def state():
        assert time.monotonic() - received < .5, "state polling stalled"
        return current

    async def wait(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(.05)
        raise AssertionError(f"timeout: {state()['autonomy']}")

    poller = asyncio.create_task(poll())
    try:
        await wait(lambda: state()["autonomy"].get("navigation", {}).get("ready"))
        async with websockets.connect("ws://127.0.0.1:8000/ws/control") as ws:
            async def send(kind, **fields):
                await ws.send(json.dumps({"type": kind, **fields}))

            async def receive():
                async for _ in ws:
                    pass

            async def heartbeat():
                while True:
                    await send("keys", keys=[])
                    await asyncio.sleep(.05)

            reader = asyncio.create_task(receive())
            ticker = asyncio.create_task(heartbeat())
            try:
                await send("claim")
                await asyncio.sleep(.15)
                await send("enable")
                await asyncio.sleep(.15)
                await send("start_autonomy")
                await wait(lambda: state()["autonomy"]["active"], 3)
                await asyncio.sleep(.5)

                # Map coordinates: right-angle turns in the clear aisle.
                # The optional route reverses, then rounds the divider.
                goals = [(0., -.6), (-.7, -.6), (-.7, .5)]
                if long_routes:
                    goals += [(-.7, -1.35), (.95, -1.35), (1.55, -.2), (1.55, 1.3)]
                if detour:
                    goals = [(1.55, 0.)]
                for index, (x, y) in enumerate(goals, 1):
                    nav = state()["autonomy"]["navigation"]
                    origin = nav["pose"]
                    dx, dy = x - origin["x"], y - origin["y"]
                    yaw = origin["yaw"]
                    forward = math.cos(yaw)*dx + math.sin(yaw)*dy
                    right = math.sin(yaw)*dx - math.cos(yaw)*dy
                    assert .1 <= math.hypot(forward, right) <= 2.
                    before = await pose()
                    bearing = before[2] + math.atan2(-right, forward)
                    target = (before[0] + math.cos(before[2])*forward + math.sin(before[2])*right,
                              before[1] + math.sin(before[2])*forward - math.cos(before[2])*right)
                    previous_request = nav["request_id"]
                    await send("navigation_goal", forward=forward, right=right)
                    await wait(lambda: state()["autonomy"]["navigation"]["request_id"] != previous_request
                               and state()["autonomy"]["navigation"]["goal_state"] == "executing", 5)
                    started = time.monotonic()
                    previous = before
                    total = forward_distance = lateral_distance = backward_distance = 0.
                    pre_alignment_distance = 0.
                    blended_distance = blended_yaw = total_yaw = 0.
                    aligned = abs(angle(before[2] - bearing)) < .35
                    onset_error = None
                    samples = 0
                    min_clearance = float("inf")
                    while time.monotonic() - started < 100:
                        actual = await pose()
                        # Known test-world geometry and a conservative 0.20 m
                        # circumscribed robot radius, including wheel spheres.
                        divider = math.hypot(max(abs(actual[0]-.7)-.075, 0),
                                             max(abs(actual[1]-.7)-1.5, 0)) - .20
                        walls = 2.925 - max(abs(actual[0]), abs(actual[1])) - .20
                        min_clearance = min(min_clearance, divider, walls)
                        local = displacement(previous, actual)
                        step = math.hypot(local[0], local[1])
                        total += step
                        forward_distance += max(0., local[0])
                        backward_distance += max(0., -local[0])
                        lateral_distance += abs(local[1])
                        total_yaw += abs(local[2])
                        # Above the stationary/noise floor at either 1x or 3x.
                        # Measure simultaneous actual displacement and yaw,
                        # not merely the controller's requested velocities.
                        if step >= .002 and abs(local[2]) >= .008:
                            blended_distance += step
                            blended_yaw += abs(local[2])
                        if not aligned:
                            pre_alignment_distance += step
                            aligned = abs(angle(actual[2] - bearing)) < .35
                        if onset_error is None and total >= .08:
                            onset_error = abs(angle(actual[2] - bearing))
                        previous = actual
                        samples += 1
                        nav = state()["autonomy"]["navigation"]
                        assert state()["autonomy"]["active"], state()["autonomy"]
                        if nav["goal_state"] in {"succeeded", "aborted", "failed", "canceled"}:
                            break
                        await asyncio.sleep(.1)
                    error = math.hypot(previous[0]-target[0], previous[1]-target[1])
                    result = dict(goal=index, map_target=[x, y], state=nav["goal_state"],
                                  seconds=round(time.monotonic()-started, 2), samples=samples,
                                  actual_pose=previous, goal_error_m=error, path_m=total,
                                  forward_share=forward_distance/max(total, .001),
                                  lateral_m=lateral_distance, backward_m=backward_distance,
                                  blended_distance_m=blended_distance,
                                  blended_yaw_rad=blended_yaw, total_yaw_rad=total_yaw,
                                  pre_alignment_m=None if detour else pre_alignment_distance,
                                  min_clearance_m=min_clearance,
                                  final_heading_error_rad=abs(angle(previous[2]-bearing)),
                                  onset_heading_error_rad=None if detour else onset_error)
                    print(json.dumps(result), flush=True)
                    assert nav["goal_state"] == "succeeded", result
                    assert error < .23, result
                    assert min_clearance > .05, result
                    assert abs(angle(previous[2]-bearing)) < .3, result
                    if not detour and index <= 2 and not baseline:
                        assert blended_distance >= .10 and blended_yaw >= .25, result
                        assert pre_alignment_distance >= .08, result
                    # Facing the route is a preference; mecanum strafe during
                    # a turn is now intentional. Reverse travel stays bounded.
                    assert backward_distance < .04, result
                    await asyncio.sleep(.3)
                print("PASS goals complete with obstacle clearance" +
                      (" (baseline measurement)" if baseline else " and blended travel"), flush=True)
            finally:
                await send("stop")
                ticker.cancel()
                reader.cancel()
                await asyncio.gather(ticker, reader, return_exceptions=True)
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)


async def main(long_routes=False, detour=False, baseline=False):
    async with pose_stream() as pose:
        await run(pose, long_routes, detour, baseline)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--long-routes", action="store_true")
    parser.add_argument("--detour", action="store_true", help="One goal across the divider; Nav2 must find its own detour")
    parser.add_argument("--baseline", action="store_true", help="Record overlap without requiring blended turns")
    args = parser.parse_args()
    asyncio.run(main(args.long_routes, args.detour, args.baseline))
