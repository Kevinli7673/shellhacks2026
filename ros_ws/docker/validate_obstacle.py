"""Insert a real Gazebo obstacle during a mission and verify Collision Monitor.

Run inside the simulation container with navigation.launch.py active and all
control dashboard tabs closed. The browser desktop can stay open. The test
always stops and removes only its own uniquely named obstacle.
"""

import asyncio
from collections import deque
import json
import math
import time
import uuid

from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.msg import CollisionMonitorState
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener
import websockets

from validate_manual import displacement, pose, state


async def gz_service(name, request_type, request):
    process = await asyncio.create_subprocess_exec(
        "gz", "service", "-s", f"/world/indoor_maze/{name}",
        "--reqtype", request_type, "--reptype", "gz.msgs.Boolean",
        "--timeout", "3000", "--req", request,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
    assert process.returncode == 0 and b"data: true" in stdout, (stdout, stderr)


async def main():
    initial = state()
    assert initial["motor"]["backend"] == "gazebo", "Gazebo only"
    assert initial["control"]["owner_session"] is None, "close the control dashboard tabs first"
    assert not initial["control"]["armed"]
    rclpy.init()
    node = rclpy.create_node("obstacle_acceptance")
    collision, safe, incoming, scans, navigation = (deque(maxlen=500) for _ in range(5))
    node.create_subscription(CollisionMonitorState, "/rescuebot/collision_state",
                             lambda m: collision.append((time.monotonic(), m)), 10)
    node.create_subscription(Twist, "/cmd_vel_safe", lambda m: safe.append((time.monotonic(), m)), 10)
    node.create_subscription(Twist, "/cmd_vel_smoothed", lambda m: incoming.append((time.monotonic(), m)), 10)
    node.create_subscription(LaserScan, "/scan", lambda m: scans.append((time.monotonic(), m)), qos_profile_sensor_data)
    node.create_subscription(String, "/rescuebot/navigation_status", lambda m: navigation.append(json.loads(m.data)), 10)
    goals = node.create_publisher(PoseStamped, "/goal_pose", 10)
    transforms = Buffer()
    listener = TransformListener(transforms, node)
    name = "acceptance_obstacle_" + uuid.uuid4().hex[:8]
    created = False

    async def spin():
        while True:
            rclpy.spin_once(node, timeout_sec=0)
            await asyncio.sleep(0.002)

    async def wait_for(predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"timed out: {state()['autonomy']}, nav={list(navigation)[-1:]}")

    spinner = asyncio.create_task(spin())
    try:
        await wait_for(lambda: scans and safe and transforms.can_transform("map", "base_link", rclpy.time.Time()))
        async with websockets.connect("ws://127.0.0.1:8000/ws/control") as ws:
            async def send(kind):
                await ws.send(json.dumps({"type": kind}))

            async def receive():
                async for _ in ws:
                    pass

            async def heartbeat():
                while True:
                    await ws.send(json.dumps({"type": "keys", "keys": []}))
                    await asyncio.sleep(0.05)

            reader = asyncio.create_task(receive())
            ticker = asyncio.create_task(heartbeat())
            try:
                await send("claim")
                await asyncio.sleep(0.1)
                await send("enable")
                await asyncio.sleep(0.15)
                await send("start_autonomy")
                await wait_for(lambda: state()["autonomy"]["active"])
                mission = state()["autonomy"]["mission"]
                await wait_for(lambda: navigation and navigation[-1]["active"] and navigation[-1]["mission"] == mission)
                transform = transforms.lookup_transform("map", "base_link", rclpy.time.Time())
                goal = PoseStamped()
                goal.header.frame_id = "map"
                goal.pose.position.x = transform.transform.translation.x - 0.8
                goal.pose.position.y = transform.transform.translation.y
                goal.pose.orientation = transform.transform.rotation
                goals.publish(goal)
                await wait_for(lambda: safe and safe[-1][1].linear.x < -0.025)
                await asyncio.sleep(0.6)
                assert state()["autonomy"]["active"]
                before = await pose()
                # Rear panel: near face is 0.28 m behind base center; the
                # wheel collision geometry extends 0.16 m along this axis.
                normal = (math.cos(before[2]), math.sin(before[2]))
                center = (before[0] - .29 * normal[0], before[1] - .29 * normal[1])
                sdf = f'''<sdf version="1.9"><model name="{name}"><static>true</static>
<pose>{center[0]} {center[1]} 0.5 0 0 {before[2]}</pose><link name="panel">
<collision name="collision"><geometry><box><size>0.02 0.5 1.0</size></box></geometry></collision>
<visual name="visual"><geometry><box><size>0.02 0.5 1.0</size></box></geometry>
<material><ambient>1 0.05 0.05 1</ambient><diffuse>1 0.05 0.05 1</diffuse></material></visual>
</link></model></sdf>'''
                inserted = time.monotonic()
                await gz_service("create", "gz.msgs.EntityFactory", "sdf: " + json.dumps(sdf))
                created = True
                print("INSERTED red obstacle during a backward Nav2 goal", flush=True)
                await wait_for(lambda: any(t >= inserted and m.action_type == m.STOP and m.polygon_name == "FootprintStop" for t, m in collision), 3)
                event_time = next(t for t, m in collision if t >= inserted and m.action_type == m.STOP)
                await wait_for(lambda: safe[-1][0] >= event_time and safe[-1][1].linear.x == safe[-1][1].linear.y == safe[-1][1].angular.z == 0)
                assert any(t >= inserted and math.hypot(m.linear.x, m.linear.y) > .01 for t, m in incoming), "no nonzero command reached the filter"
                stopped = await pose()
                await asyncio.sleep(0.8)
                after = await pose()
                delta = displacement(stopped, after)
                assert math.hypot(delta[0], delta[1]) < .005, delta
                # Conservative projection of body corners and wheel spheres
                # onto the obstacle normal verifies positive separation.
                relative_yaw = after[2] - before[2]
                c, s = abs(math.cos(relative_yaw)), abs(math.sin(relative_yaw))
                extent = max(.15 * c + .125 * s, .11 * c + .10 * s + .05)
                separation = (after[0] - center[0]) * normal[0] + (after[1] - center[1]) * normal[1]
                clearance = separation - .01 - extent
                assert clearance > .05, f"insufficient obstacle clearance: {clearance} m"
                print(f"PASS FootprintStop event in {event_time-inserted:.3f}s; safe velocity zero; clearance={clearance:.4f}m; stopped drift={delta}", flush=True)
                print("Holding the visible obstacle for 5 seconds; test ends with Stop", flush=True)
                await asyncio.sleep(5)
            finally:
                await send("stop")
                ticker.cancel()
                reader.cancel()
                await asyncio.gather(ticker, reader, return_exceptions=True)
    finally:
        if created:
            await gz_service("remove", "gz.msgs.Entity", f'name: "{name}" type: MODEL')
        spinner.cancel()
        await asyncio.gather(spinner, return_exceptions=True)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
