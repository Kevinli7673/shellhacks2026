"""Measure world-clock acceleration and unchanged driving speed in Gazebo.

Run with a fresh stopped world and no simulation dashboard owner. Leaves the
world at 3x, driving disarmed; follow with navigation/obstacle acceptance.
"""

import asyncio
import argparse
import json
import math
import time

import websockets

from validate_manual import displacement, pose_stream, state


async def main(set_only=None):
    async with asyncio.timeout(60):
        while True:
            try:
                initial = await asyncio.to_thread(state)
                if (initial.get("simulation_playback", {}).get("actual") is not None
                        and initial.get("autonomy", {}).get("navigation", {}).get("ready")):
                    break
            except OSError:
                pass
            await asyncio.sleep(.2)
    assert initial["motor"]["backend"] == "gazebo"
    assert initial["control"]["owner_session"] is None, "close simulation control tab"
    assert not initial["control"]["armed"]
    async with pose_stream() as pose, websockets.connect("ws://127.0.0.1:8000/ws/control") as ws:
        keys, responses = [], asyncio.Queue()

        async def read():
            async for raw in ws:
                message = json.loads(raw)
                if message["type"] != "state":
                    await responses.put(message)

        async def send(kind, **fields):
            await ws.send(json.dumps({"type": kind, **fields}))

        async def command(kind, **fields):
            await send(kind, **fields)
            async with asyncio.timeout(3):
                while True:
                    response = await responses.get()
                    if response["type"] == kind:
                        return response["accepted"]

        async def heartbeat():
            while True:
                await send("keys", keys=keys.copy())
                await asyncio.sleep(.05)

        async def playback():
            value = (await asyncio.to_thread(state))["simulation_playback"]
            assert value["actual"] is not None, value
            return value

        reader = asyncio.create_task(read())
        ticker = asyncio.create_task(heartbeat())
        results = []
        try:
            assert await command("claim")
            for rate in ((set_only,) if set_only is not None else (1, 2, 3)):
                assert await command("simulation_playback", rate=rate)
                async with asyncio.timeout(5):
                    while (p := await playback())["pending"]:
                        await asyncio.sleep(.05)
                assert p["target"] == rate and p["error"] is None, p
                if set_only is not None:
                    print(json.dumps(p), flush=True)
                    return
                await asyncio.sleep(2)
                start, wall = (await playback())["sim_seconds"], time.monotonic()
                await asyncio.sleep(4)
                achieved = ((await playback())["sim_seconds"] - start) / (time.monotonic() - wall)
                # Target RTF is an upper bound, not a performance guarantee.
                # Require useful acceleration on this validation host while
                # retaining the measured result when CPU cannot reach 3x.
                assert (.75 if rate == 1 else 1.2) < achieved < 1.15 * rate, (rate, achieved)
                speeds = []
                for key in ("KeyW", "KeyS"):
                    assert await command("enable")
                    assert not await command("simulation_playback", rate=1), "rate changed while armed"
                    before, clock = await pose(), (await playback())["sim_seconds"]
                    keys[:] = [key]
                    async with asyncio.timeout(5):
                        while (elapsed := (await playback())["sim_seconds"] - clock) < 2:
                            await asyncio.sleep(.02)
                    keys.clear()
                    assert await command("stop")
                    after = await pose()
                    delta = displacement(before, after)
                    measured = math.hypot(delta[0], delta[1]) / elapsed
                    assert .085 < measured < .145, (rate, key, measured, delta)
                    speeds.append(measured)
                    await asyncio.sleep(.4)
                    held = await pose()
                    await asyncio.sleep(.6)
                    assert math.dist(held[:2], (await pose())[:2]) < .005, "Stop drift"
                results.append({"requested": rate, "actual": achieved, "meters_per_sim_second": speeds})
                print(json.dumps(results[-1]), flush=True)
            assert not state()["control"]["armed"]
            print("PASS clock acceleration, unchanged movement speed, armed-rate rejection, Stop hold", flush=True)
        finally:
            keys.clear()
            await send("stop")
            await asyncio.sleep(.1)
            ticker.cancel()
            reader.cancel()
            await asyncio.gather(ticker, reader, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-only", type=int, choices=(1, 2, 3), help="set playback without driving")
    asyncio.run(main(parser.parse_args().set_only))
