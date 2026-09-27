"""Pure conversions between the dashboard's data and ROS conventions (no ROS imports).

Dashboard LiDAR bins (app/rescuebot/lidar_scan.py): 360 values in mm, bin i
covers [i, i+1) degrees measured *clockwise* from straight ahead, 0 = no
reading. ROS LaserScan angles grow *counterclockwise* (REP 103), so the scan
is mirrored: ROS ray k (angle -0.5 + k degrees) is dashboard bin (360 - k) % 360,
whose center sits at -(360 - k + 0.5) = k - 0.5 degrees.

The BNO055 heading is in degrees clockwise, so ROS yaw is its negative.
"""

from __future__ import annotations

import math
import struct
import zlib
from typing import Sequence

BIN_COUNT = 360
SCAN_ANGLE_MIN = math.radians(-0.5)
SCAN_ANGLE_INCREMENT = math.radians(1.0)


def bins_to_ranges(bins: Sequence[int], range_min_m: float, range_max_m: float) -> list[float]:
    """Dashboard bins (mm, clockwise) -> LaserScan ranges (m, counterclockwise from angle_min)."""
    if len(bins) != BIN_COUNT:
        raise ValueError(f"expected {BIN_COUNT} bins, got {len(bins)}")
    ranges = []
    for k in range(BIN_COUNT):
        mm = bins[(BIN_COUNT - k) % BIN_COUNT]
        metres = mm / 1000.0 if isinstance(mm, (int, float)) and mm > 0 else 0.0
        ranges.append(metres if range_min_m <= metres <= range_max_m else math.inf)
    return ranges


def heading_to_yaw(heading_deg: float, reference_deg: float) -> float:
    """Clockwise compass heading -> counterclockwise ROS yaw relative to the start, in [-pi, pi)."""
    yaw = -math.radians(heading_deg - reference_deg)
    return (yaw + math.pi) % (2 * math.pi) - math.pi


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    """(x, y, z, w) for a rotation about +z."""
    return 0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


UNKNOWN_RGB = (205, 205, 205)
FREE_RGB = (254, 254, 254)
ROBOT_RGB = (220, 30, 30)


def occupancy_to_rgb(value: int) -> tuple[int, int, int]:
    if value < 0:
        return UNKNOWN_RGB
    shade = round(254 * (1 - min(value, 100) / 100))
    return shade, shade, shade


def map_to_rgb_rows(
    width: int,
    height: int,
    data: Sequence[int],
    robot_cell: tuple[int, int] | None = None,
    robot_radius_cells: int = 2,
) -> list[bytes]:
    """OccupancyGrid data (row 0 = bottom) -> RGB rows for an image (row 0 = top)."""
    if len(data) != width * height:
        raise ValueError("data length does not match width * height")
    rows = []
    for image_row in range(height):
        grid_row = height - 1 - image_row
        row = bytearray()
        for col in range(width):
            row.extend(occupancy_to_rgb(data[grid_row * width + col]))
        rows.append(row)
    if robot_cell is not None:
        cx, cy = robot_cell
        for dy in range(-robot_radius_cells, robot_radius_cells + 1):
            for dx in range(-robot_radius_cells, robot_radius_cells + 1):
                x, y = cx + dx, cy + dy
                if dx * dx + dy * dy <= robot_radius_cells ** 2 and 0 <= x < width and 0 <= y < height:
                    offset = 3 * x
                    rows[height - 1 - y][offset:offset + 3] = bytes(ROBOT_RGB)
    return [bytes(row) for row in rows]


def encode_png(width: int, height: int, rgb_rows: Sequence[bytes]) -> bytes:
    """Minimal 8-bit RGB PNG encoder (no Pillow in the image)."""
    if len(rgb_rows) != height or any(len(row) != 3 * width for row in rgb_rows):
        raise ValueError("row data does not match the image size")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    raw = b"".join(b"\x00" + row for row in rgb_rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def world_to_cell(x: float, y: float, origin_x: float, origin_y: float, resolution: float) -> tuple[int, int]:
    return int(math.floor((x - origin_x) / resolution)), int(math.floor((y - origin_y) / resolution))


CAMERA_HFOV_DEG = 66.0  # Raspberry Pi AI Camera; same value as app/rescuebot/voice.py
PERSON_RGB = (30, 90, 230)


def x_to_bearing(x_norm: float, hfov_deg: float = CAMERA_HFOV_DEG) -> float:
    """Image x (0 = left edge, 1 = right) -> degrees, right (clockwise) positive.

    Same formula as app/rescuebot/voice.py x_to_bearing.
    """
    half_width = math.tan(math.radians(hfov_deg / 2))
    return math.degrees(math.atan((x_norm - 0.5) * 2 * half_width))


def range_at_bearing(bins: Sequence[int] | None, bearing_deg: float, window_deg: int = 3,
                     range_max_m: float = 12.0) -> float | None:
    """Nearest LiDAR distance (m) within +/- window_deg of a clockwise bearing, or None."""
    if not bins or len(bins) != BIN_COUNT:
        return None
    center = int(math.floor(bearing_deg)) % BIN_COUNT
    readings = [bins[(center + d) % BIN_COUNT] for d in range(-window_deg, window_deg + 1)]
    metres = [mm / 1000.0 for mm in readings if isinstance(mm, (int, float)) and 0 < mm <= range_max_m * 1000]
    return min(metres) if metres else None


def bearing_range_to_xy(bearing_deg: float, range_m: float) -> tuple[float, float]:
    """Clockwise bearing + range -> (x forward, y left) in base_link (REP 103)."""
    angle = -math.radians(bearing_deg)
    return range_m * math.cos(angle), range_m * math.sin(angle)
