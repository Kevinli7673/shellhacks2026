"""Validate dashboard API driving against the simulated model's actual pose.

Run inside the simulation container. This checks the API path, not browser
keyboard handling. It refuses to issue input unless the backend is Gazebo.
"""

import asyncio
import json
import math
import urllib.request

import websockets


def state():
    with urllib.request.urlopen("http://127.0.0.1:8000/api/state", timeout=2) as response:
        return json.load(response)


async def pose():
    process = await asyncio.create_subprocess_exec(
        "gz", "topic", "-e", "-n", "1", "--json-output",
        "-t", "/world/indoor_maze/dynamic_pose/info",
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), 5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError("Gazebo pose query failed")
    # gz topic can deliver a second queued JSON record before honoring -n 1.
    sample, _ = json.JSONDecoder().raw_decode(raw.decode().lstrip())
    model = next(p for p in sample["pose"] if p["name"] == "rescuebot")
    p, q = model["position"], model["orientation"]
    yaw = math.atan2(
        2 * (q["w"] * q.get("z", 0) + q.get("x", 0) * q.get("y", 0)),
        1 - 2 * (q.get("y", 0) ** 2 + q.get("z", 0) ** 2),
    )
    return p.get("x", 0), p.get("y", 0), yaw


def displacement(before, after):
    dx, dy = after[0] - before[0], after[1] - before[1]
    heading = before[2]
    return (
        math.cos(heading) * dx + math.sin(heading) * dy,
        -math.sin(heading) * dx + math.cos(heading) * dy,
        math.atan2(math.sin(after[2] - heading), math.cos(after[2] - heading)),
    )


async def main():
    assert state()["motor"]["backend"] == "gazebo", "simulation backend required"
    await pose()  # Require a live simulator before claiming control.
    async with websockets.connect("ws://127.0.0.1:8000/ws/control") as ws:
        keys = []
        heartbeat_enabled = True

        async def send(kind, **fields):
            await ws.send(json.dumps({"type": kind, **fields}))

        async def receive():
            async for _ in ws:
                pass

        async def speed(percent):
            for _ in range(10):
                current = state()["control"]["speed_percent"]
                if current == percent:
                    return
                await send("speed", delta=10 if percent > current else -10)
                await asyncio.sleep(0.1)
            raise AssertionError("speed setting was not accepted")

        async def heartbeat():
            while True:
                if heartbeat_enabled:
                    await send("keys", keys=keys.copy())
                await asyncio.sleep(0.05)

        reader = asyncio.create_task(receive())
        await send("claim")
        await asyncio.sleep(0.1)
        ticker = asyncio.create_task(heartbeat())
        try:
            await send("enable")
            await asyncio.sleep(0.15)
            assert state()["control"]["armed"], "could not enable simulation"
            await speed(30)

            async def move(key, duration=0.8):
                before = await pose()
                keys[:] = [key]
                await asyncio.sleep(duration)
                keys.clear()
                await asyncio.sleep(0.25)
                delta = displacement(before, await pose())
                print(json.dumps({"key": key, "forward_m": delta[0], "left_m": delta[1], "ccw_rad": delta[2]}), flush=True)
                return delta

            for key, index, sign, minimum in (
                ("KeyW", 0, 1, 0.025), ("KeyS", 0, -1, 0.025),
                ("KeyA", 1, 1, 0.025), ("KeyD", 1, -1, 0.025),
                ("ArrowLeft", 2, 1, 0.10), ("ArrowRight", 2, -1, 0.10),
            ):
                delta = await move(key)
                assert delta[index] * sign > minimum, f"wrong or insufficient physical simulation motion for {key}: {delta}"

            stationary = await pose()
            await speed(40)
            await asyncio.sleep(0.4)
            still = displacement(stationary, await pose())
            assert math.hypot(still[0], still[1]) < 0.01 and abs(still[2]) < 0.05
            fast = (await move("KeyW"))[0]
            await move("KeyS")
            await speed(20)
            slow = (await move("KeyW"))[0]
            await move("KeyS")
            assert fast > slow * 1.3, f"speed setting did not affect movement: {fast}, {slow}"

            keys[:] = ["KeyW"]
            await asyncio.sleep(0.2)
            await send("stop")
            await asyncio.sleep(0.35)
            assert not state()["control"]["armed"]
            stopped = await pose()
            await asyncio.sleep(0.4)
            delta = displacement(stopped, await pose())
            assert math.hypot(delta[0], delta[1]) < 0.01
            keys.clear()
            await asyncio.sleep(0.1)
            await send("enable")
            await asyncio.sleep(0.1)
            assert state()["control"]["armed"]
            keys[:] = ["KeyW"]
            await asyncio.sleep(0.2)
            heartbeat_enabled = False
            await asyncio.sleep(0.6)
            assert not state()["control"]["armed"], "host input expiry did not disarm"
            print("PASS: model motion, speed, release, Stop while held, and input expiry", flush=True)
        finally:
            keys.clear()
            await send("stop")
            ticker.cancel()
            reader.cancel()
            await asyncio.gather(ticker, reader, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
