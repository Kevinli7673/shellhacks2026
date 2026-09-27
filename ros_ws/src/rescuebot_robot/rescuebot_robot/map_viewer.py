"""Serve the live SLAM map as a web page (no RViz needed on the headless Pi).

http://<pi>:8090/          auto-refreshing page
http://<pi>:8090/map.png   latest map: robot = red dot, people seen recently = blue dots
http://<pi>:8090/map.json  size, resolution, robot pose, people, and update age

People come from /rescuebot/people (camera bearing + LiDAR range, in
base_link) and are placed on the map with the robot's SLAM pose.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import threading
import time

from geometry_msgs.msg import PoseArray
from nav_msgs.msg import OccupancyGrid
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener

from .conversions import PERSON_RGB, encode_png, map_to_rgb_rows, quaternion_to_yaw, world_to_cell

PEOPLE_MEMORY_S = 60.0  # keep a sighting on the map this long
PEOPLE_MERGE_M = 0.5  # sightings closer than this are the same person

PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rescuebot Map</title>
<style>
  body { margin: 0; padding: 16px; font: 15px system-ui, sans-serif; background: #111; color: #eee; }
  img { display: block; max-width: 100%; width: 720px; image-rendering: pixelated; background: #cdcdcd; }
  p { color: #aaa; }
</style></head>
<body>
<h1>Rescuebot live map</h1>
<p id="info">Waiting for the first map&hellip;</p>
<img id="map" alt="SLAM map; the red dot is the robot">
<p>Grey = unexplored, white = free, black = obstacle, red = robot, blue = person seen in the last minute. Drive slowly for a clean map.</p>
<script>
async function refresh() {
  try {
    const info = await (await fetch("map.json", { cache: "no-store" })).json();
    if (info.width) {
      document.getElementById("map").src = "map.png?t=" + Date.now();
      const pose = info.robot ? `robot x ${info.robot.x.toFixed(2)} m, y ${info.robot.y.toFixed(2)} m` : "robot pose unknown";
      const people = info.people && info.people.length
        ? `, ${info.people.length} person(s): ` + info.people.map(p => `(${p.x.toFixed(1)}, ${p.y.toFixed(1)})`).join(" ")
        : "";
      document.getElementById("info").textContent =
        `${(info.width * info.resolution).toFixed(1)} x ${(info.height * info.resolution).toFixed(1)} m, ` +
        `${pose}${people}, updated ${info.age_s.toFixed(0)} s ago`;
    }
  } catch (e) { document.getElementById("info").textContent = "Map server unreachable"; }
}
refresh(); setInterval(refresh, 2000);
</script>
</body></html>
"""


class MapViewer(Node):
    def __init__(self) -> None:
        super().__init__("rescuebot_map_viewer")
        port = int(self.declare_parameter("port", 8090).value)
        self.map_frame = self.declare_parameter("map_frame", "map").value
        self.base_frame = self.declare_parameter("base_frame", "base_link").value
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(OccupancyGrid, "map", self._on_map, qos)
        self.create_subscription(PoseArray, "rescuebot/people", self._on_people, 10)
        self._people: list[dict] = []  # map-frame sightings: x, y, seen (monotonic)
        self._lock = threading.Lock()
        self._map: OccupancyGrid | None = None
        self._map_received = 0.0
        self._png: bytes | None = None
        self._png_key: tuple | None = None

        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/":
                    self._send(200, "text/html; charset=utf-8", PAGE)
                elif path == "/map.json":
                    self._send(200, "application/json", json.dumps(viewer.info()).encode())
                elif path == "/map.png":
                    png = viewer.png()
                    self._send(200, "image/png", png) if png else self._send(404, "text/plain", b"no map yet")
                else:
                    self._send(404, "text/plain", b"not found")

            def _send(self, code: int, kind: str, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="map-http", daemon=True).start()
        self.get_logger().info(f"Map page: http://<this-pi>:{port}/")

    def _on_map(self, msg: OccupancyGrid) -> None:
        with self._lock:
            self._map = msg
            self._map_received = time.monotonic()

    def _on_people(self, msg: PoseArray) -> None:
        robot = self._robot()
        if robot is None or not msg.poses:
            return
        c, s = math.cos(robot["yaw"]), math.sin(robot["yaw"])
        now = time.monotonic()
        with self._lock:
            for pose in msg.poses:
                px, py = pose.position.x, pose.position.y
                x, y = robot["x"] + c * px - s * py, robot["y"] + s * px + c * py
                for known in self._people:
                    if math.hypot(known["x"] - x, known["y"] - y) < PEOPLE_MERGE_M:
                        known.update(x=x, y=y, seen=now)
                        break
                else:
                    self._people.append({"x": x, "y": y, "seen": now})
                    self.get_logger().info(f"Person seen at map x {x:.2f} m, y {y:.2f} m")

    def _recent_people(self) -> list[dict]:
        cutoff = time.monotonic() - PEOPLE_MEMORY_S
        with self._lock:
            self._people = [p for p in self._people if p["seen"] >= cutoff]
            return [dict(p) for p in self._people]

    def _robot(self) -> dict | None:
        try:
            t = self.tf_buffer.lookup_transform(self.map_frame, self.base_frame, Time())
        except TransformException:
            return None
        r = t.transform.rotation
        return {"x": t.transform.translation.x, "y": t.transform.translation.y,
                "yaw": quaternion_to_yaw(r.x, r.y, r.z, r.w)}

    def info(self) -> dict:
        with self._lock:
            grid, received = self._map, self._map_received
        if grid is None:
            return {"width": 0}
        return {
            "width": grid.info.width, "height": grid.info.height, "resolution": grid.info.resolution,
            "origin": {"x": grid.info.origin.position.x, "y": grid.info.origin.position.y},
            "robot": self._robot(), "age_s": time.monotonic() - received,
            "people": [{"x": p["x"], "y": p["y"], "age_s": time.monotonic() - p["seen"]}
                       for p in self._recent_people()],
        }

    def png(self) -> bytes | None:
        with self._lock:
            grid, received = self._map, self._map_received
        if grid is None:
            return None
        robot = self._robot()
        cell = None
        if robot is not None:
            cell = world_to_cell(robot["x"], robot["y"], grid.info.origin.position.x,
                                 grid.info.origin.position.y, grid.info.resolution)
        people = [world_to_cell(p["x"], p["y"], grid.info.origin.position.x, grid.info.origin.position.y,
                                grid.info.resolution) for p in self._recent_people()]
        key = (received, cell, tuple(people))
        if key != self._png_key:
            rows = map_to_rgb_rows(grid.info.width, grid.info.height, grid.data, cell)
            rows = [bytearray(row) for row in rows]
            for px, py in people:
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        x, y = px + dx, py + dy
                        if dx * dx + dy * dy <= 4 and 0 <= x < grid.info.width and 0 <= y < grid.info.height:
                            rows[grid.info.height - 1 - y][3 * x:3 * x + 3] = bytes(PERSON_RGB)
            self._png = encode_png(grid.info.width, grid.info.height, [bytes(r) for r in rows])
            self._png_key = key
        return self._png

    def destroy_node(self) -> None:
        self._server.shutdown()
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MapViewer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
