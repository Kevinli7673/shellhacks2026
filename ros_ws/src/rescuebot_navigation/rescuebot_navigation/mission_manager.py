"""Own simulation goals, map search, and cancellation through the host arbiter."""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import OccupancyGrid
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener

from rescuebot.bridge_ipc import DatagramReceiver, DatagramSender
from rescuebot.navigation_ipc import (
    SEARCH_BUSY, STATUS_TIMEOUT_S, decode_navigation_goal, encode_navigation, navigation_socket,
)
from rescuebot_navigation.search import Coverage, Grid, is_valid_viewpoint, next_viewpoint, observe


@dataclass(frozen=True)
class SearchPreparation:
    coverage: Coverage
    grid: Grid
    origin: tuple[float, float, float]
    point: tuple[float, float] | None
    planned: bool
    projected: bool


def prepare_search(grid, origin, coverage, observations, visited, rejected,
                   *, planned=True, projected_view=None, candidate=None):
    """Run sensing and planning off the status/command timer on frozen inputs."""
    for observed_grid, position in observations:
        coverage = observe(observed_grid, position, coverage)
    point = None
    if planned:
        if (candidate is not None and projected_view is None
                and is_valid_viewpoint(grid, origin, candidate, rejected,
                                       coverage=coverage, visited=visited)):
            point = candidate
        else:
            point = next_viewpoint(grid, origin, visited, rejected,
                                   coverage=coverage, projected_view=projected_view)
    return SearchPreparation(coverage, grid, origin, point, planned, projected_view is not None)


class MissionManager(Node):
    def __init__(self, goal_socket=None, status_socket=None) -> None:
        super().__init__("rescuebot_mission_manager")
        self._active = False
        self._mission = None
        self._last_status = None
        self._generation = 0
        self._pending = False
        self._goal_handle = None
        self._goal_state = "idle"
        self._last_request = None
        self._last_safe_velocity = None
        self._search_enabled = self.declare_parameter("search_enabled", False).value
        # Synthetic detector fixtures only; none of these values reach planning.
        self._target = (self.declare_parameter("synthetic_target_x", 1.8).value,
                        self.declare_parameter("synthetic_target_y", .6).value)
        self._target_enabled = self.declare_parameter("synthetic_target_enabled", True).value
        if not all(math.isfinite(v) for v in self._target):
            raise ValueError("Synthetic target coordinates must be finite")
        self._search_tree = str(Path(get_package_share_directory("rescuebot_navigation"))
                                / "behavior_trees" / "search_viewpoint.xml")
        self._grid = None
        self._map_received = None
        self._planner = ThreadPoolExecutor(max_workers=1)
        self._plan = None
        # Keep the running future even when its mission is canceled. The executor
        # must never accumulate work from repeated Stop/start cycles.
        self._worker = None
        self._prefetched = None
        self._coverage = Coverage()
        self._observations = deque(maxlen=16)
        self._last_observation = None
        self._planning_started = 0.
        self._visited = []
        self._rejected = []
        self._viewpoint = None
        self._search = {"available": False, "phase": "idle", "found": False,
                        "home": None, "target": None, "visited": 0, "reason": ""}
        self._goal_receiver = DatagramReceiver(goal_socket or navigation_socket("navigation-goal.sock"))
        self._status_sender = DatagramSender(status_socket or navigation_socket("navigation-status.sock"))
        self._transforms = Buffer(node=self)
        self._transform_listener = TransformListener(self._transforms, self)
        self._action = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._idle_velocity = self.create_publisher(Twist, "/cmd_vel_nav", 10)
        self._goal_status = self.create_publisher(String, "/rescuebot/navigation_status", 10)
        self.create_subscription(PoseStamped, "/goal_pose", self._goal, 10)
        self.create_subscription(String, "/rescuebot/autonomy_status", self._status, 10)
        self.create_subscription(Twist, "/cmd_vel_safe", self._safe_velocity, 10)
        self.create_subscription(OccupancyGrid, "/map", self._map,
                                 QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                                            reliability=ReliabilityPolicy.RELIABLE))
        self.create_timer(0.05, self._tick)

    def destroy_node(self):
        self._planner.shutdown(wait=False, cancel_futures=True)
        self._goal_receiver.close()
        self._status_sender.close()
        return super().destroy_node()

    def _safe_velocity(self, _message):
        self._last_safe_velocity = time.monotonic()

    def _map(self, message):
        info = message.info
        q = info.origin.orientation
        if (message.header.frame_id != "map" or not .025 <= info.resolution <= .2
                or not 0 < info.width*info.height <= 250000
                or len(message.data) != info.width*info.height
                or not all(math.isfinite(v) for v in (info.origin.position.x, info.origin.position.y, q.x, q.y, q.z, q.w))
                or abs(abs(q.w)-1) > .001
                or abs(q.x)+abs(q.y)+abs(q.z) > .001):
            return
        grid = Grid(info.width, info.height, info.resolution,
                    info.origin.position.x, info.origin.position.y,
                    tuple(0 if 0 <= value < 50 else 100 if value >= 50 else -1
                          for value in message.data))
        # Probability-only changes within the same free/occupied/unknown class
        # do not change sensing or clearance and need not invalidate a plan.
        if grid != self._grid:
            self._grid = grid
        self._map_received = time.monotonic()

    def _map_pose(self):
        try:
            transform = self._transforms.lookup_transform("map", "base_link", rclpy.time.Time())
        except TransformException:
            return None
        stamp = transform.header.stamp
        age = self.get_clock().now().nanoseconds / 1e9 - (stamp.sec + stamp.nanosec / 1e9)
        if not -0.1 <= age < 0.5:
            return None
        p, q = transform.transform.translation, transform.transform.rotation
        return {"x": p.x, "y": p.y,
                "yaw": math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y*q.y + q.z*q.z))}

    def _readiness(self, now, pose):
        if self._last_status is None or now - self._last_status >= 0.25:
            return "Waiting for dashboard"
        if not self._action.server_is_ready():
            return "Waiting for Nav2"
        if pose is None:
            return "Waiting for a fresh SLAM pose"
        if self._last_safe_velocity is None or now - self._last_safe_velocity >= 0.25:
            return "Waiting for Collision Monitor"
        return "Ready"

    def _dashboard_goals(self, now, pose, ready):
        for raw in self._goal_receiver.drain():
            try:
                # TF/readiness work can overlap an arrival or expiry. Compare
                # the deadline after receiving, not with the earlier tick time.
                record = decode_navigation_goal(raw, time.monotonic())
            except (ValueError, TypeError, KeyError):
                continue
            if (not ready or not self._active or record["mission"] != self._mission
                    or record["request_id"] == self._last_request
                    or self._search["phase"] in SEARCH_BUSY
                    or self._pending or self._goal_handle is not None):
                continue
            if record.get("task") == "search":
                if self._start_search(now, pose):
                    self._last_request = record["request_id"]
                continue
            self._last_request = record["request_id"]
            goal = PoseStamped()
            goal.header.frame_id = "map"
            goal.header.stamp = self.get_clock().now().to_msg()
            forward, right, yaw = record["forward"], record["right"], pose["yaw"]
            # Dashboard axes: forward/right; ROS body axes: forward/left.
            goal.pose.position.x = pose["x"] + math.cos(yaw)*forward + math.sin(yaw)*right
            goal.pose.position.y = pose["y"] + math.sin(yaw)*forward - math.cos(yaw)*right
            # Finish facing the requested destination, rather than preserving
            # the starting heading and encouraging a sideways approach.
            heading = yaw + math.atan2(-right, forward)
            goal.pose.orientation.z = math.sin(heading / 2)
            goal.pose.orientation.w = math.cos(heading / 2)
            self._goal(goal)

    def _status(self, message: String) -> None:
        try:
            record = json.loads(message.data)
            active, mission = record["active"], record["mission"]
            if not isinstance(active, bool) or (active and not isinstance(mission, str)):
                raise ValueError("invalid mission status")
        except (KeyError, TypeError, ValueError):
            self.get_logger().warning("discarded malformed autonomy status")
            return
        if self._mission != mission or (self._active and not active):
            self._cancel_search()
            self._cancel_goal()
            self._last_request = None
            if active:
                self._goal_state = "idle"
                self._search.update(phase="idle", found=False, home=None, target=None, visited=0, reason="")
        self._active, self._mission = active, mission
        self._last_status = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        host_fresh = self._last_status is not None and now - self._last_status < 0.25
        if not host_fresh:
            if self._active:
                self._cancel_search()
                self._cancel_goal()
                self._active = False
        pose = self._map_pose()
        reason = self._readiness(now, pose)
        self._search["available"] = (self._search_enabled and self._map_received is not None
                                      and now-self._map_received < 3)
        self._dashboard_goals(now, pose, reason == "Ready")
        self._search_tick(now, pose)
        if host_fresh and not self._pending and self._goal_handle is None:
            # Only idle after search has had a chance to hand off a prepared
            # goal. Never mask missing output from an in-progress controller.
            self._idle_velocity.publish(Twist())
        record = {
            "mission": self._mission, "active": self._active,
            "goal_state": self._goal_state,
            "ready": reason == "Ready", "reason": reason, "pose": pose,
            "request_id": self._last_request, "expires_at": now + STATUS_TIMEOUT_S,
            "search": self._search,
        }
        self._goal_status.publish(String(data=json.dumps(record)))
        self._status_sender.send(encode_navigation(record))

    def _goal(self, pose: PoseStamped, *, search=False) -> None:
        if self._search["phase"] in SEARCH_BUSY and not search:
            return
        if not self._active or self._last_status is None or time.monotonic() - self._last_status >= 0.25:
            self.get_logger().warning("ignored goal_pose until the operator starts autonomy")
            return
        if self._pending or self._goal_handle is not None:
            self.get_logger().warning("stop the current goal before selecting another")
            return
        if not self._action.server_is_ready():
            self.get_logger().error("NavigateToPose action server is unavailable")
            return
        self._generation += 1
        generation = self._generation
        self._pending = True
        self._goal_state = "pending"
        goal = NavigateToPose.Goal()
        goal.pose = pose
        if search and self._search["phase"] == "exploring":
            goal.behavior_tree = self._search_tree
        future = self._action.send_goal_async(goal)
        future.add_done_callback(lambda result: self._goal_response(result, generation))

    def _goal_response(self, future, generation) -> None:
        try:
            handle = future.result()
        except Exception as exc:
            if generation == self._generation:
                self._pending = False
                self._goal_state = "failed"
            self.get_logger().error(f"goal request failed: {exc}")
            return
        # An asynchronous acceptance can arrive after Stop/manual override.
        if generation != self._generation or not self._active:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        self._pending = False
        if not handle.accepted:
            self._goal_state = "rejected"
            return
        self._goal_handle = handle
        self._goal_state = "executing"
        handle.get_result_async().add_done_callback(
            lambda result: self._goal_finished(result, generation))
        if self._search["phase"] in {"notifying", "return_pending"}:
            handle.cancel_goal_async()

    def _goal_finished(self, future, generation) -> None:
        if generation != self._generation:
            return
        self._goal_handle = None
        try:
            status = future.result().status
            self._goal_state = {4: "succeeded", 5: "canceled", 6: "aborted"}.get(status, "failed")
        except Exception:
            self._goal_state = "failed"
        self.get_logger().info(f"navigation goal {self._goal_state}")

    def _cancel_goal(self) -> None:
        self._generation += 1
        self._pending = False
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()
            self._goal_handle = None
        self._goal_state = "canceled"

    def _cancel_search(self):
        if self._search["phase"] in SEARCH_BUSY:
            self._search.update(phase="canceled", reason="Search canceled by operator or source loss")
        self._discard_search_work()

    def _discard_search_work(self):
        if self._plan is not None:
            self._plan.cancel()
            self._plan = None
        self._prefetched = None
        self._observations.clear()

    def _start_search(self, now, pose):
        if not self._search["available"]:
            return False
        self._discard_search_work()
        self._coverage = Coverage()
        self._last_observation = None
        self._visited = [(pose["x"], pose["y"])]
        self._rejected = []
        self._viewpoint = None
        self._failures = self._goal_count = 0
        self._search_started = now
        self._search_pose = (now, dict(pose))
        self._goal_state = "idle"
        self._search.update(phase="exploring", found=False, home=dict(pose), target=None,
                            visited=1, reason="Searching mapped free space")
        self.get_logger().info(f"search started; home=({pose['x']:.3f}, {pose['y']:.3f}, {pose['yaw']:.3f})")
        return True

    def _search_destination(self, x, y, yaw, now):
        goal = PoseStamped()
        goal.header.frame_id = "map"
        goal.header.stamp = self.get_clock().now().to_msg()
        goal.pose.position.x, goal.pose.position.y = float(x), float(y)
        goal.pose.orientation.z = math.sin(yaw/2)
        goal.pose.orientation.w = math.cos(yaw/2)
        self._leg_started = now
        self._goal(goal, search=True)

    def _begin_return(self, now, reason, *, found=False):
        self.get_logger().info(f"search ending after {self._goal_count} goals: {reason}")
        self._search.update(phase="notifying" if found else "return_pending", reason=reason)
        self._transition_started = now
        self._discard_search_work()
        if self._goal_handle is not None:
            # Wait for the action's terminal result before submitting home.
            # A late acceptance is canceled in _goal_response instead.
            self._goal_handle.cancel_goal_async()

    def _fail_search(self, reason):
        self._discard_search_work()
        self._search.update(phase="failed", reason=reason)
        self._cancel_goal()

    def _search_tick(self, now, pose):
        phase = self._search["phase"]
        if not self._active or phase not in SEARCH_BUSY:
            return
        if pose is None or not self._search["available"]:
            self._fail_search("SLAM pose or map became stale; stopped")
            return
        position = (pose["x"], pose["y"])
        previous_time, previous_pose = self._search_pose
        dt = max(0., now-previous_time)
        jump = math.dist(position, (previous_pose["x"], previous_pose["y"]))
        turn = abs(math.atan2(math.sin(pose["yaw"]-previous_pose["yaw"]), math.cos(pose["yaw"]-previous_pose["yaw"])))
        if jump > .30 + .12*dt or turn > .45 + .30*dt:
            self._fail_search("Localization jumped; stopped. Restart simulation before searching")
            return
        self._search_pose = (now, dict(pose))
        if phase == "exploring":
            if math.dist(position, self._visited[-1]) >= .20 and len(self._visited) < 1024:
                self._visited.append(position)
                self._search["visited"] = len(self._visited)
            self._record_observation(position)
            self._collect_preparation()
            if self._search["phase"] != "exploring":
                return
            # A 360-degree, 0.9 m synthetic proximity detector. Both range and
            # an entirely observed free ray are required; unknown cells and
            # walls occlude the target. This is not a camera/person detector.
            if (self._target_enabled and math.dist(position, self._target) <= .9
                    and self._grid.visible(position, self._target)):
                self._search.update(found=True, target={"x": self._target[0], "y": self._target[1], "yaw": 0.})
                self._begin_return(now, "Simulated person found; returning to start", found=True)
                return
            if now-self._search_started > 600 or self._goal_count >= 48:
                self._begin_return(now, "Search limit reached; target not found")
                return
        if phase in {"notifying", "return_pending"}:
            if self._pending or self._goal_handle is not None:
                if now-self._transition_started > 5:
                    self._fail_search("Navigation cancellation timed out; stopped")
                return
            if now-self._transition_started < 1:
                return
            self._search["phase"] = "returning"
            home = self._search["home"]
            self._search_destination(home["x"], home["y"], home["yaw"], now)
            return
        if self._pending or self._goal_handle is not None:
            if phase == "exploring":
                self._prepare_next(now, pose, moving=True)
            if now-self._leg_started > (240 if phase == "returning" else 90):
                if phase == "returning":
                    self._fail_search("Return route timed out; stopped away from start")
                else:
                    self._begin_return(now, "Search route timed out; returning without target")
            return
        if phase == "returning":
            home = self._search["home"]
            error = math.dist(position, (home["x"], home["y"]))
            heading = abs(math.atan2(math.sin(pose["yaw"]-home["yaw"]), math.cos(pose["yaw"]-home["yaw"])))
            if self._goal_state == "succeeded" and error <= .20 and heading <= .30:
                self._search.update(phase="complete", reason=("Target found; returned to start" if self._search["found"]
                                                              else "Returned to start. " + self._search["reason"]))
                self.get_logger().info(self._search["reason"])
            else:
                self._fail_search("Return route failed; stopped away from start")
            return
        if self._goal_state in {"aborted", "failed", "rejected", "canceled"}:
            # This destination was not reached: its projected successor is no
            # longer applicable, and late worker output must be discarded.
            self._discard_search_work()
            if self._viewpoint is not None:
                self._rejected.append(self._viewpoint)
            self._failures += 1
        elif self._goal_state == "succeeded":
            self._failures = 0
        self._goal_state = "idle"
        if self._failures >= 3:
            self._begin_return(now, "Three search routes failed; returning without target")
            return
        prepared = self._prefetched
        if (prepared is not None and prepared.grid is self._grid
                and prepared.coverage == self._coverage and not self._observations
                and self._plan is None
                and math.dist(prepared.origin[:2], position) <= .25
                and (prepared.point is not None or not prepared.projected)):
            self._prefetched = None
            point = prepared.point
            if point is None:
                self._begin_return(now, "No more useful reachable viewpoints; target not found")
                return
            self._viewpoint = point
            self._goal_count += 1
            self._search_destination(*point, math.atan2(point[1]-pose["y"], point[0]-pose["x"]), now)
            self.get_logger().info(f"search waypoint {self._goal_count}: ({point[0]:.3f}, {point[1]:.3f})")
            return
        self._prepare_next(now, pose, moving=False)

    def _record_observation(self, position):
        previous = self._last_observation
        if previous is None or previous[0] is not self._grid or math.dist(previous[1], position) >= .10:
            # Capture the map when sensing occurs. A map received later must
            # never retroactively make old unknown/occluded space "searched".
            observation = (self._grid, position)
            self._observations.append(observation)
            self._last_observation = observation

    def _collect_preparation(self):
        if self._plan is None or not self._plan.done():
            return
        try:
            result = self._plan.result()
        except Exception as exc:
            self.get_logger().error(f"search planning failed: {exc}")
            self._fail_search("Search planning failed; stopped")
            return
        self._plan = None
        self._coverage = result.coverage
        if result.planned and self._plan_goal_generation == self._generation:
            self._prefetched = result

    def _prepare_next(self, now, pose, *, moving):
        if self._plan is not None or (self._worker is not None and not self._worker.done()):
            return
        if moving and self._viewpoint is None:
            return
        origin = (pose["x"], pose["y"], pose["yaw"])
        projected = None
        visited = tuple(self._visited)
        if moving:
            projected = self._viewpoint
            origin = (*projected, math.atan2(projected[1]-pose["y"], projected[0]-pose["x"]))
            visited += (projected,)
        prepared = self._prefetched
        planned = (not moving or prepared is None
                   or ((prepared.grid is not self._grid or self._observations
                        or prepared.coverage != self._coverage)
                       and now-self._planning_started >= .5))
        if not planned and not self._observations:
            return
        observations = tuple(self._observations)
        self._observations.clear()
        candidate = prepared.point if prepared is not None and not moving else None
        self._plan = self._planner.submit(
            prepare_search, self._grid, origin, self._coverage, observations,
            visited, tuple(self._rejected), planned=planned,
            projected_view=projected, candidate=candidate)
        self._worker = self._plan
        self._plan_goal_generation = self._generation
        if planned:
            self._planning_started = now


def main() -> None:
    rclpy.init()
    node = MissionManager()
    try:
        rclpy.spin(node)
    finally:
        node._cancel_goal()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
