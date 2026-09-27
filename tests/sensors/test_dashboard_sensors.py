"""LiDAR panel: parser, child processes, and dashboard wiring."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

from rescuebot import lidar_scan
from rescuebot.sensor_process import SensorProcess
from rescuebot.sensors import (
    PACKAGE_ROOT,
    LiveSensors,
    MockSensors,
    OffSensors,
    create_sensors,
)
from rescuebot.service import RobotControlService
from rescuebot.web import _dashboard_state, create_app
from rescuebot.replay_camera import MockCameraBackend


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def run_module(module: str, args: list[str], stdin: bytes) -> list[dict]:
    env = {"PYTHONPATH": PACKAGE_ROOT, "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, "-m", module, *args], input=stdin, capture_output=True, timeout=20, env=env
    )
    return [json.loads(line) for line in result.stdout.decode().splitlines() if line.strip()]


def ultra_simple_lines(scans: int, points_per_scan: int = 360, dist: float = 1500.0) -> str:
    lines = []
    for scan in range(scans):
        for i in range(points_per_scan):
            theta = i * 360 / points_per_scan
            prefix = "S" if i == 0 else " "
            lines.append(f"{prefix}  theta: {theta:.2f} Dist: {dist + scan:08.2f} Q: 47 ")
    return "\n".join(lines) + "\n"


class LidarScanTest(unittest.TestCase):
    def test_parse_line(self):
        self.assertEqual(lidar_scan.parse_line("S  theta: 12.34 Dist: 01234.00 Q: 47 "), (12.34, 1234.0, 47))
        self.assertIsNone(lidar_scan.parse_line("RPLIDAR S/N: 1234"))

    def test_scan_assembler_splits_on_wrap_and_drops_invalid(self):
        assembler = lidar_scan.ScanAssembler()
        self.assertIsNone(assembler.add(10, 1000, 47))
        self.assertIsNone(assembler.add(200, 0, 47))       # no distance
        self.assertIsNone(assembler.add(300, 1000, 0))     # no quality
        self.assertIsNone(assembler.add(350, 99999, 47))   # beyond rated range
        done = assembler.add(5, 800, 47)
        self.assertEqual(done, [(10, 1000)])

    def test_bin_scan_keeps_nearest_per_degree_and_applies_offset(self):
        bins = lidar_scan.bin_scan([(10.2, 1500), (10.8, 900), (359.5, 700)], offset_deg=0)
        self.assertEqual(len(bins), 360)
        self.assertEqual(bins[10], 900)
        self.assertEqual(bins[359], 700)
        rotated = lidar_scan.bin_scan([(10.2, 1500)], offset_deg=180)
        self.assertEqual(rotated[190], 1500)
        self.assertEqual(lidar_scan.nearest(bins), {"angle": 359, "mm": 700})
        self.assertIsNone(lidar_scan.nearest([0] * 360))

    def test_process_prints_binned_scans(self):
        records = run_module("rescuebot.lidar_scan", ["--stdin", "--max-rate", "0"], ultra_simple_lines(3).encode())
        # The last scan never wraps, so only complete scans are printed.
        self.assertEqual([r["type"] for r in records], ["lidar_scan", "lidar_scan"])
        self.assertEqual(records[0]["points"], 360)
        self.assertEqual(records[0]["bins"][0], 1500)
        self.assertEqual(records[1]["nearest"]["mm"], 1501)

    def test_tool_output_before_scans_is_reported(self):
        text = "SLAMTEC LIDAR S/N: 1234\nError, cannot bind to the specified serial port /dev/x.\n"
        records = run_module("rescuebot.lidar_scan", ["--stdin"], text.encode())
        self.assertEqual(records, [{"type": "sensor_message", "message": "LiDAR tool: SLAMTEC LIDAR S/N: 1234"}])

    def test_missing_tool_reports_a_message(self):
        records = run_module("rescuebot.lidar_scan", ["--bin", "/nonexistent/ultra_simple"], b"")
        self.assertEqual(records[0]["type"], "sensor_message")
        self.assertIn("not found", records[0]["message"])


FAKE_SENSOR = textwrap.dedent('''
    import json, sys, time
    print("not json", flush=True)
    print(json.dumps({"type": "sensor_message", "message": "warming up"}), flush=True)
    for i in range(COUNT):
        print(json.dumps({"type": "reading", "value": i}), flush=True)
        time.sleep(0.05)
    print(json.dumps({"type": "sensor_message", "message": "device unplugged"}), flush=True)
    time.sleep(EXIT_AFTER)
    sys.exit(3)
''')


class SensorProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fake(self, count=1000, exit_after=0.0):
        path = Path(self.tmp.name) / "fake_sensor.py"
        path.write_text(f"COUNT = {count}\nEXIT_AFTER = {exit_after}\n" + FAKE_SENSOR, encoding="utf-8")
        return [sys.executable, "-u", str(path)]

    def test_online_with_newest_record(self):
        process = SensorProcess("Fake", self.fake(), "reading", expiry_s=1.0)
        process.start()
        self.addCleanup(process.close)
        self.assertTrue(wait_for(lambda: process.snapshot()[0] == "online"))
        status, _message, record, age_ms = process.snapshot()
        self.assertEqual(record["type"], "reading")
        self.assertLess(age_ms, 1000)
        self.assertEqual(process.counters()["invalid_lines"], 1)

    def test_goes_stale_then_offline_with_the_childs_reason(self):
        process = SensorProcess("Fake", self.fake(count=2, exit_after=0.8), "reading", expiry_s=0.3)
        process.start()
        self.addCleanup(process.close)
        self.assertTrue(wait_for(lambda: process.snapshot()[0] == "stale"))
        self.assertEqual(process.snapshot()[1], "device unplugged")
        self.assertTrue(wait_for(lambda: process.snapshot()[0] == "offline"))
        self.assertEqual(process.snapshot()[1], "device unplugged")

    def test_close_stops_the_child(self):
        process = SensorProcess("Fake", self.fake(), "reading")
        process.start()
        self.assertTrue(wait_for(lambda: process.snapshot()[0] == "online"))
        process.close()
        self.assertEqual(process.snapshot()[0], "offline")

    def test_start_failure_is_offline_not_an_exception(self):
        process = SensorProcess("Fake", ["/nonexistent/sensor-binary"], "reading")
        process.start()
        status, message, record, _ = process.snapshot()
        self.assertEqual((status, record), ("offline", None))
        self.assertIn("could not start", message)


class DashboardSensorsTest(unittest.TestCase):
    def test_modes(self):
        self.assertIsInstance(create_sensors("off"), OffSensors)
        self.assertIsInstance(create_sensors("mock"), MockSensors)
        self.assertIsInstance(create_sensors("live"), LiveSensors)
        with self.assertRaises(ValueError):
            create_sensors("radar")

    def test_state_includes_sensors_and_defaults_to_off(self):
        camera = MockCameraBackend()
        self.assertEqual(_dashboard_state(RobotControlService(), None, camera)["sensors"], {"mode": "off"})
        sensors = _dashboard_state(RobotControlService(), None, camera, MockSensors())["sensors"]
        self.assertEqual(set(sensors), {"mode", "lidar"})
        self.assertEqual(sensors["lidar"]["status"], "online")
        self.assertNotIn("bins", sensors["lidar"])  # the full scan stays out of the 10 Hz state

    def test_mock_lidar_scan(self):
        scan = MockSensors().lidar()
        self.assertEqual(len(scan["bins"]), 360)
        self.assertEqual(scan["bins"][0], 1800)   # front wall
        self.assertEqual(scan["bins"][180], 1200)  # back wall
        self.assertEqual(scan["nearest"], {"angle": 25, "mm": 900})

    def test_create_app_wires_sensors(self):
        app = create_app(sensors_mode="mock")
        self.assertIsInstance(app.state.sensor_service, MockSensors)
        self.assertIn("/api/lidar", {route.path for route in app.routes})
        self.assertIsInstance(create_app().state.sensor_service, OffSensors)

    def test_live_lidar_without_hardware_reports_why(self):
        # Without the LiDAR tool, the panel goes offline with the child's
        # explanation instead of failing the dashboard.
        sensors = LiveSensors()
        sensors.lidar_process = SensorProcess(
            "LiDAR", [sys.executable, "-m", "rescuebot.lidar_scan", "--bin", "/nonexistent/ultra_simple"],
            "lidar_scan", env={"PYTHONPATH": PACKAGE_ROOT},
        )
        sensors.start()
        self.addCleanup(sensors.close)
        self.assertTrue(wait_for(lambda: "not found" in sensors.status()["lidar"]["message"], timeout=10))
        self.assertEqual(sensors.status()["lidar"]["status"], "offline")
        self.assertIsNone(sensors.lidar()["bins"])


if __name__ == "__main__":
    unittest.main()
