import math
import struct
import zlib

import pytest

from rescuebot_robot.conversions import (
    ROBOT_RGB,
    SCAN_ANGLE_INCREMENT,
    SCAN_ANGLE_MIN,
    bins_to_ranges,
    encode_png,
    heading_to_yaw,
    map_to_rgb_rows,
    quaternion_to_yaw,
    world_to_cell,
    yaw_to_quaternion,
)


def ray_angle_deg(k: int) -> float:
    return math.degrees(SCAN_ANGLE_MIN + k * SCAN_ANGLE_INCREMENT)


def test_front_reading_stays_in_front():
    bins = [0] * 360
    bins[0] = 1500
    ranges = bins_to_ranges(bins, 0.08, 12.0)
    assert ranges[0] == 1.5
    assert abs(ray_angle_deg(0)) <= 0.5
    assert sum(math.isfinite(r) for r in ranges) == 1


def test_clockwise_bins_become_counterclockwise_rays():
    # Dashboard bin 90 = 90 degrees clockwise = the robot's right = ROS -90 degrees.
    bins = [0] * 360
    bins[90] = 2000
    ranges = bins_to_ranges(bins, 0.08, 12.0)
    k = ranges.index(2.0)
    assert ray_angle_deg(k) % 360 == pytest.approx(269.5)  # bin 90 center (90.5 cw) = -90.5 ccw


def test_missing_and_out_of_range_readings_are_inf():
    bins = [0] * 360
    bins[10], bins[20], bins[30] = 50, 13000, 800
    ranges = bins_to_ranges(bins, 0.08, 12.0)
    assert ranges[360 - 10] == math.inf
    assert ranges[360 - 20] == math.inf
    assert ranges[360 - 30] == 0.8
    with pytest.raises(ValueError):
        bins_to_ranges([0] * 359, 0.08, 12.0)


def test_clockwise_heading_is_negative_yaw_relative_to_start():
    assert heading_to_yaw(100.0, 100.0) == 0.0
    assert heading_to_yaw(190.0, 100.0) == pytest.approx(-math.pi / 2)
    assert heading_to_yaw(10.0, 100.0) == pytest.approx(math.pi / 2)
    assert heading_to_yaw(5.0, 355.0) == pytest.approx(math.radians(-10))


def test_quaternion_round_trip():
    for yaw in (-3.0, -1.0, 0.0, 0.7, 3.0):
        assert quaternion_to_yaw(*yaw_to_quaternion(yaw)) == pytest.approx(yaw)


def test_map_rows_flip_vertically_and_draw_the_robot():
    # 2 x 2 grid, row 0 is the bottom: bottom-left occupied, top-right unknown.
    rows = map_to_rgb_rows(2, 2, [100, 0, 0, -1])
    assert rows[1][0:3] == bytes((0, 0, 0))  # bottom-left in the last image row
    assert rows[0][3:6] == bytes((205, 205, 205))
    with_robot = map_to_rgb_rows(5, 5, [0] * 25, robot_cell=(2, 2), robot_radius_cells=0)
    assert with_robot[2][6:9] == bytes(ROBOT_RGB)
    with pytest.raises(ValueError):
        map_to_rgb_rows(2, 2, [0, 0, 0])


def test_png_is_valid():
    rows = [bytes((255, 0, 0)) * 3, bytes((0, 0, 255)) * 3]
    png = encode_png(3, 2, rows)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (3, 2)
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41:41 + idat_len])
    assert raw == b"\x00" + rows[0] + b"\x00" + rows[1]


def test_world_to_cell():
    assert world_to_cell(0.0, 0.0, -1.0, -2.0, 0.05) == (20, 40)
