"""Exercise real SLAM/Nav2 goals through the dashboard's Gazebo-only arbiter.

Requires navigation.launch.py, a fresh simulation near its spawn, and no other
browser owner. Always sends Stop. No serial or physical backend is accepted.
"""

import asyncio
import json
import math
import time

from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import OccupancyGrid
from lifecycle_msgs.srv import GetState
from nav2_msgs.srv import ManageLifecycleNodes
import rclpy
from rclpy.qos import QoSProfile, DurabilityPolicy
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener
import websockets

from validate_manual import state as fetch_state, pose, displacement


async def main():
    latest_state = fetch_state()
    state_received = time.monotonic()
    assert latest_state["motor"]["backend"] == "gazebo"

    def state():
        assert time.monotonic() - state_received < .5, "dashboard state polling stalled"
        return latest_state

    async def poll_state():
        nonlocal latest_state, state_received
        while True:
            # HTTP reads must not starve the 50 ms WebSocket heartbeat.
            latest_state = await asyncio.to_thread(fetch_state)
            state_received = time.monotonic()
            await asyncio.sleep(.05)
    rclpy.init()
    node = rclpy.create_node("navigation_acceptance")
    maps, statuses = [], []
    node.create_subscription(OccupancyGrid, "/map", maps.append,
                             QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
    node.create_subscription(String, "/rescuebot/navigation_status",
                             lambda m: statuses.append(json.loads(m.data)), 10)
    goal_publisher = node.create_publisher(PoseStamped, "/goal_pose", 10)
    transforms = Buffer()
    listener = TransformListener(transforms, node)

    async def spin():
        while True:
            await asyncio.to_thread(rclpy.spin_once, node, timeout_sec=.01)

    spinner = asyncio.create_task(spin())
    poller = asyncio.create_task(poll_state())

    async def wait_for(predicate, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"timeout: host={state()}, navigation={statuses[-1:]}")

    try:
        await wait_for(lambda: maps and statuses)
        await wait_for(lambda: transforms.can_transform("map", "base_link", rclpy.time.Time()), 15)
        m = maps[-1]
        assert sum(v >= 0 for v in m.data) > 100
        print(f"MAP {m.info.width}x{m.info.height}, known={sum(v >= 0 for v in m.data)}", flush=True)
        async with websockets.connect("ws://127.0.0.1:8000/ws/control") as ws:
            keys = []

            async def send(kind, **fields):
                await ws.send(json.dumps({"type": kind, **fields}))

            async def receive():
                async for _ in ws:
                    pass

            async def heartbeat():
                while True:
                    await send("keys", keys=keys.copy())
                    await asyncio.sleep(0.05)

            reader = asyncio.create_task(receive())
            await send("claim")
            await asyncio.sleep(0.1)
            ticker = asyncio.create_task(heartbeat())
            try:
                async def start():
                    await send("enable")
                    await asyncio.sleep(0.15)
                    await send("start_autonomy")
                    await wait_for(lambda: state()["autonomy"]["active"], 2)
                    await asyncio.sleep(1)
                    assert state()["autonomy"]["active"], "idle mission expired"

                async def goal(dy):
                    transform = transforms.lookup_transform("map", "base_link", rclpy.time.Time())
                    p = PoseStamped()
                    p.header.frame_id = "map"
                    p.pose.position.x = transform.transform.translation.x
                    p.pose.position.y = transform.transform.translation.y + dy
                    p.pose.orientation.w = 1.0
                    statuses.clear()
                    goal_publisher.publish(p)
                    await wait_for(lambda: any(s["goal_state"] == "executing" for s in statuses), 5)
                    print(f"GOAL x={p.pose.position.x:.3f}, y={p.pose.position.y:.3f}", flush=True)

                await start()
                before = await pose()
                await goal(-0.6)
                await wait_for(lambda: statuses and statuses[-1]["goal_state"] in {"succeeded", "aborted", "failed", "canceled"}, 45)
                assert statuses[-1]["goal_state"] == "succeeded", statuses[-1]
                delta = displacement(before, await pose())
                assert delta[1] < -0.4, delta
                assert state()["autonomy"]["active"]
                print(f"PASS goal succeeded; actual model displacement={delta}", flush=True)

                await goal(0.5)
                await asyncio.sleep(1)
                await send("stop")
                await wait_for(lambda: not state()["control"]["armed"])
                await asyncio.sleep(0.4)
                stopped = await pose()
                await asyncio.sleep(0.5)
                delta = displacement(stopped, await pose())
                assert math.hypot(delta[0], delta[1]) < 0.01
                assert not state()["autonomy"]["active"]
                print("PASS Stop cancels navigation and holds the model stationary", flush=True)

                await start()
                await goal(0.5)
                await asyncio.sleep(0.5)
                keys[:] = ["KeyS"]
                await asyncio.sleep(0.2)
                assert not state()["autonomy"]["active"]
                assert state()["autonomy"]["reason"] == "manual_override"
                keys.clear()
                await asyncio.sleep(0.5)
                assert not state()["autonomy"]["active"]
                print("PASS manual override cancels goal without automatic resume", flush=True)

                await send("stop")
                await asyncio.sleep(0.2)
                await start()
                await goal(0.4)
                await asyncio.sleep(0.5)
                lifecycle = node.create_client(
                    ManageLifecycleNodes, "/lifecycle_manager_navigation/manage_nodes")
                await wait_for(lifecycle.service_is_ready)

                def transition(command):
                    request = ManageLifecycleNodes.Request()
                    request.command = command
                    return lifecycle.call_async(request)

                # Use the lifecycle manager so its bond bookkeeping stays in
                # sync. Directly toggling one managed node leaves a delayed
                # bond failure that can deactivate the rest of the stack.
                started = time.monotonic()
                paused = transition(ManageLifecycleNodes.Request.PAUSE)
                try:
                    await wait_for(lambda: not state()["control"]["armed"], 3)
                    elapsed = time.monotonic() - started
                    assert state()["autonomy"]["reason"] == "autonomy_timeout"
                    await wait_for(paused.done)
                    assert paused.result().success
                    print(f"PASS managed Nav2 pause disarms in {elapsed:.3f}s", flush=True)
                finally:
                    await wait_for(paused.done)
                    resumed = transition(ManageLifecycleNodes.Request.RESUME)
                    await wait_for(resumed.done)
                    assert resumed.result().success
                await asyncio.sleep(5)
                assert not state()["control"]["armed"]
                assert not state()["autonomy"]["active"]
                for name in ("controller_server", "planner_server", "behavior_server",
                             "bt_navigator", "velocity_smoother", "collision_monitor"):
                    client = node.create_client(GetState, f"/{name}/get_state")
                    await wait_for(client.service_is_ready)
                    result = client.call_async(GetState.Request())
                    await wait_for(result.done)
                    assert result.result().current_state.id == 3, f"{name} did not remain active"
                print("PASS all six Nav2 nodes remain active after managed resume", flush=True)
                print("PASS restored source never automatically rearms", flush=True)
            finally:
                await send("stop")
                ticker.cancel()
                reader.cancel()
                await asyncio.gather(ticker, reader, return_exceptions=True)
    finally:
        spinner.cancel()
        poller.cancel()
        await asyncio.gather(spinner, poller, return_exceptions=True)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
