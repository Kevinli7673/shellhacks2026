"""Check forward-facing dashboard goals against Gazebo ground truth.

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

from validate_manual import displacement, pose, state as fetch_state


def angle(value):
    return math.atan2(math.sin(value), math.cos(value))


async def main(long_routes=False):
    current = await asyncio.to_thread(fetch_state)
    assert current["motor"]["backend"] == "gazebo"
    assert current["control"]["owner_session"] is None, "close simulation control tabs"
    assert not current["control"]["armed"]
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
                    aligned = abs(angle(before[2] - bearing)) < .35
                    onset_error = None
                    samples = 0
                    while time.monotonic() - started < 100:
                        actual = await pose()
                        local = displacement(previous, actual)
                        step = math.hypot(local[0], local[1])
                        total += step
                        forward_distance += max(0., local[0])
                        backward_distance += max(0., -local[0])
                        lateral_distance += abs(local[1])
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
                                  pre_alignment_m=pre_alignment_distance,
                                  onset_heading_error_rad=onset_error)
                    print(json.dumps(result), flush=True)
                    assert nav["goal_state"] == "succeeded", result
                    assert error < .23, result
                    assert pre_alignment_distance < .08, result
                    assert onset_error is not None and onset_error < .4, result
                    assert forward_distance / total > .80, result
                    assert backward_distance < .04, result
                    await asyncio.sleep(.3)
                print("PASS goals align before travel and predominantly drive forward", flush=True)
            finally:
                await send("stop")
                ticker.cancel()
                reader.cancel()
                await asyncio.gather(ticker, reader, return_exceptions=True)
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--long-routes", action="store_true")
    asyncio.run(main(parser.parse_args().long_routes))
