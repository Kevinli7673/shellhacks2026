"""Exercise search/notification/return through the dashboard and Gazebo truth.

Use the search_house world with control tabs closed. No direct velocity or
Nav2 goals are sent. Every exit sends Stop. Actual obstacle clearance, sensor
range/occlusion, saved home, disarming, and a second start are checked.
"""

import asyncio
import argparse
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import websockets

from validate_manual import displacement, pose_stream, state as fetch_state


def obstacles():
    world = Path(__file__).parents[1]/"src/rescuebot_gazebo/worlds/search_house.sdf"
    boxes = []
    for model in ET.parse(world).findall("world/model"):
        size = model.find("link/collision/geometry/box/size")
        if size is not None:
            x, y, *_ = map(float, model.findtext("pose").split())
            sx, sy, _ = map(float, size.text.split())
            boxes.append((x, y, sx/2, sy/2))
    return boxes


def clearance(point, boxes):
    return min(math.hypot(max(abs(point[0]-x)-sx, 0), max(abs(point[1]-y)-sy, 0))-.20
               for x, y, sx, sy in boxes)


async def run(pose, north_start=False):
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
        assert time.monotonic()-received < .5, "dashboard telemetry stalled"
        return current

    def search():
        return state()["autonomy"]["navigation"].get("search", {})

    async def wait(predicate, timeout=30):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            if predicate():
                return
            await asyncio.sleep(.05)
        raise AssertionError(f"timeout: {state()['autonomy']}")

    poller = asyncio.create_task(poll())
    try:
        await wait(lambda: state()["autonomy"]["navigation"]["ready"] and search().get("available"))
        async with websockets.connect("ws://127.0.0.1:8000/ws/control") as ws:
            async def send(kind, **fields):
                await ws.send(json.dumps(dict(type=kind, **fields)))

            async def receive():
                async for message in ws:
                    value = json.loads(message)
                    if value.get("accepted") is False:
                        print("REJECTED", message, flush=True)

            async def heartbeat():
                while True:
                    await send("keys", keys=[])
                    await asyncio.sleep(.05)

            async def enable_autonomy():
                await send("enable")
                await wait(lambda: state()["control"]["armed"], 3)
                await send("start_autonomy")
                await wait(lambda: state()["autonomy"]["active"] and
                           state()["autonomy"]["navigation"].get("mission") == state()["autonomy"]["mission"], 3)

            async def start():
                await enable_autonomy()
                await send("start_search")
                await wait(lambda: search().get("phase") == "exploring", 3)

            reader = asyncio.create_task(receive())
            ticker = asyncio.create_task(heartbeat())
            try:
                await send("claim")
                await asyncio.sleep(.2)
                if north_start:
                    # Reproduce the reported northern start using dashboard
                    # goals, without teleporting the model or resetting SLAM.
                    # Run near the normal spawn or after a spawn-start search.
                    await enable_autonomy()
                    for x, y in ((-.55, 1.), (-.55, 2.15)):
                        nav = state()["autonomy"]["navigation"]
                        p = nav["pose"]
                        dx, dy = x-p["x"], y-p["y"]
                        assert .1 < math.hypot(dx, dy) <= 2., "north-start setup requires a pose near spawn"
                        previous_request = nav.get("request_id")
                        await send("navigation_goal",
                                   forward=math.cos(p["yaw"])*dx+math.sin(p["yaw"])*dy,
                                   right=math.sin(p["yaw"])*dx-math.cos(p["yaw"])*dy)
                        await wait(lambda: state()["autonomy"]["navigation"].get("request_id") not in
                                   {None, previous_request}, 5)
                        await wait(lambda: state()["autonomy"]["navigation"]["goal_state"] in
                                   {"succeeded", "aborted", "failed", "canceled"}, 90)
                        assert state()["autonomy"]["navigation"]["goal_state"] == "succeeded"
                    await send("stop")
                    await wait(lambda: not state()["control"]["armed"], 3)
                    await asyncio.sleep(.5)
                    print(json.dumps(dict(event="north_start_ready", pose=await pose())), flush=True)
                await start()
                await asyncio.sleep(2)
                await send("stop")
                await wait(lambda: not state()["control"]["armed"] and search()["phase"] == "canceled", 3)
                await asyncio.sleep(.5)
                stopped = await pose()
                await asyncio.sleep(.8)
                assert math.dist(stopped[:2], (await pose())[:2]) < .005
                print("PASS Stop during search; restarting explicitly", flush=True)

                home = await pose()
                await start()
                previous = home
                started = last_report = time.monotonic()
                path = forward = 0.
                minimum = float("inf")
                maximum_pose_error = 0.
                boxes = obstacles()
                found_pose = None
                phases = []
                while time.monotonic()-started < 850:
                    actual = await pose()
                    local = displacement(previous, actual)
                    path += math.hypot(*local[:2])
                    forward += max(0., local[0])
                    previous = actual
                    minimum = min(minimum, clearance(actual, boxes))
                    assert minimum > .05, ("insufficient clearance", actual, minimum)
                    s = search()
                    estimate = state()["autonomy"]["navigation"]["pose"]
                    if estimate is not None:
                        maximum_pose_error = max(maximum_pose_error, math.dist(actual[:2], (estimate["x"], estimate["y"])))
                        assert maximum_pose_error < .25, ("SLAM pose diverged", actual, estimate)
                    if not phases or phases[-1] != s["phase"]:
                        phases.append(s["phase"])
                    if s.get("found") and found_pose is None:
                        found_pose = actual
                        target = (s["target"]["x"], s["target"]["y"])
                        assert math.dist(actual[:2], target) < 1., ("target reported out of range", actual)
                        for i in range(101):
                            ray = (actual[0]+(target[0]-actual[0])*i/100,
                                   actual[1]+(target[1]-actual[1])*i/100)
                            assert clearance(ray, boxes) > -.195, ("target reported through obstacle", actual)
                        print(json.dumps(dict(event="simulated_person_found", pose=actual, target=target)), flush=True)
                    if time.monotonic()-last_report > 5:
                        print(json.dumps(dict(phase=s["phase"], pose=actual, visited=s["visited"],
                                              goal=state()["autonomy"]["navigation"]["goal_state"])), flush=True)
                        last_report = time.monotonic()
                    if s["phase"] in {"complete", "failed", "canceled"}:
                        break
                    assert state()["autonomy"]["active"], state()["autonomy"]
                    await asyncio.sleep(.1)
                home_error = math.dist(previous[:2], home[:2])
                heading_error = abs(displacement(home, previous)[2])
                result = dict(phase=search()["phase"], found=search()["found"], phases=phases,
                              seconds=time.monotonic()-started, path_m=path,
                              forward_share=forward/max(path, .001), home_error_m=home_error,
                              home_heading_error_rad=heading_error, minimum_clearance_m=minimum,
                              max_slam_error_m=maximum_pose_error, found_pose=found_pose)
                print(json.dumps(result), flush=True)
                assert search()["phase"] == "complete" and search()["found"], result
                assert home_error < .20 and heading_error < .30, result
                assert forward/path > .8, result
                await wait(lambda: not state()["control"]["armed"] and not state()["autonomy"]["active"], 3)
                await asyncio.sleep(.5)
                stopped = await pose()
                await asyncio.sleep(.8)
                assert math.dist(stopped[:2], (await pose())[:2]) < .005
                await start()
                assert not search()["found"], "new mission retained previous detection"
                await send("stop")
                await wait(lambda: search()["phase"] == "canceled", 3)
                print("PASS search, notification, return, disarm, and repeat start", flush=True)
            finally:
                await send("stop")
                reader.cancel()
                ticker.cancel()
                await asyncio.gather(reader, ticker, return_exceptions=True)
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)


async def main(north_start=False):
    async with pose_stream() as pose:
        await run(pose, north_start)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--north-start", action="store_true",
                        help="Drive from near spawn to the reported northern start before testing search")
    asyncio.run(main(parser.parse_args().north_start))
