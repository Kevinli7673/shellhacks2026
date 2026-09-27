"""Coordinate conversion at the ROS autonomy boundary.

ROS uses +X forward, +Y left, and +Z counterclockwise.  Rescuebot's
lower-level command convention is +forward, +sideways to the physical right,
and +turn clockwise.  This module deliberately contains no wheel mixing:
mixing remains the responsibility of the existing mock/firmware logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RobotMotion:
    """Normalized Rescuebot motion request consumed by the host arbiter."""

    forward: float = 0.0
    sideways: float = 0.0
    turn: float = 0.0


@dataclass(frozen=True)
class RosVelocity:
    """ROS planar velocity in metres/second and radians/second."""

    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def _positive(value: float, name: str) -> float:
    value = _finite(value, name)
    if value <= 0.0:
        raise ValueError(f"{name} must be positive")
    return value


def _clamp_axis(value: float) -> float:
    return max(-1.0, min(1.0, value))


def ros_to_robot_motion(
    linear_x: float,
    linear_y: float,
    angular_z: float,
    *,
    max_vx: float,
    max_vy: float,
    max_wz: float,
) -> RobotMotion:
    """Convert ROS ``Twist`` values to bounded logical robot motion.

    ``max_*`` values are the Nav2/simulation velocity limits.  Clamping is a
    source-boundary safety guard; it is not mecanum wheel saturation.
    """

    linear_x = _finite(linear_x, "linear_x")
    linear_y = _finite(linear_y, "linear_y")
    angular_z = _finite(angular_z, "angular_z")
    max_vx = _positive(max_vx, "max_vx")
    max_vy = _positive(max_vy, "max_vy")
    max_wz = _positive(max_wz, "max_wz")
    return RobotMotion(
        forward=_clamp_axis(linear_x / max_vx),
        sideways=_clamp_axis(-linear_y / max_vy),
        turn=_clamp_axis(-angular_z / max_wz),
    )


def robot_to_ros_velocity(
    forward: float,
    sideways: float,
    turn: float,
    *,
    max_vx: float,
    max_vy: float,
    max_wz: float,
) -> RosVelocity:
    """Convert a bounded logical command back to ROS ``Twist`` coordinates."""

    forward = _clamp_axis(_finite(forward, "forward"))
    sideways = _clamp_axis(_finite(sideways, "sideways"))
    turn = _clamp_axis(_finite(turn, "turn"))
    max_vx = _positive(max_vx, "max_vx")
    max_vy = _positive(max_vy, "max_vy")
    max_wz = _positive(max_wz, "max_wz")
    return RosVelocity(
        linear_x=forward * max_vx,
        linear_y=-sideways * max_vy,
        angular_z=-turn * max_wz,
    )
