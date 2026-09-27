"""Webcam, microphone, and LiDAR panels: parsers, child processes, and dashboard wiring."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

from rescuebot import audio_level, lidar_scan, webcam_stream
from rescuebot.sensor_process import SensorProcess
from rescuebot.sensors import (
    HEARING_THRESHOLD_DBFS,
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

    def test_missing_tool_reports_a_message(self):
        records = run_module("rescuebot.lidar_scan", ["--bin", "/nonexistent/ultra_simple"], b"")
        self.assertEqual(records[0]["type"], "sensor_message")
        self.assertIn("not found", records[0]["message"])


class AudioLevelTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(audio_level.chunk_levels(b"\x00\x00" * 1600), (-90.0, -90.0))
        full = struct.pack("<2h", 32767, -32768) * 800
        rms, peak = audio_level.chunk_levels(full)
        self.assertAlmostEqual(rms, 0.0, delta=0.1)
        self.assertAlmostEqual(peak, 0.0, delta=0.1)
        tone = struct.pack(f"<{1600}h", *(int(3277 * math.sin(i / 5)) for i in range(1600)))
        rms, _ = audio_level.chunk_levels(tone)
        self.assertAlmostEqual(rms, -23.0, delta=0.5)  # 0.1 of full scale, sine RMS

    def test_finds_webcam_card(self):
        cards = textwrap.dedent("""\
             0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                                  vc4-hdmi-0
             2 [BRIO           ]: USB-Audio - Logitech BRIO
                                  Logitech BRIO at usb-xhci-hcd.0-1, super speed
            """)
        self.assertEqual(audio_level.find_webcam_card(cards), "BRIO")
        self.assertIsNone(audio_level.find_webcam_card(" 0 [vc4hdmi0 ]: vc4-hdmi - vc4-hdmi-0\n"))

    def test_process_prints_a_level_per_100ms(self):
        quiet = b"\x00\x00" * 1600
        loud = struct.pack("<2h", 16000, -16000) * 800
        records = run_module("rescuebot.audio_level", ["--stdin"], quiet + loud)
        self.assertEqual([r["type"] for r in records], ["audio_level", "audio_level"])
        self.assertLess(records[0]["rms_dbfs"], HEARING_THRESHOLD_DBFS)
        self.assertGreater(records[1]["rms_dbfs"], HEARING_THRESHOLD_DBFS)


def fake_jpeg(width: int, height: int, body: bytes = b"\x01\x02") -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + sof + b"\xff\xda\x00\x02" + body + b"\xff\xd9"


class WebcamStreamTest(unittest.TestCase):
    def test_split_jpegs_across_reads_and_garbage(self):
        a, b = fake_jpeg(640, 360), fake_jpeg(320, 180, b"\xff\x00\x05")
        stream = io.BufferedReader(io.BytesIO(b"junk" + a + b"\x00" + b), buffer_size=7)
        frames = list(webcam_stream.split_jpegs(stream, read_size=5))
        self.assertEqual(frames, [a, b])
        self.assertEqual(webcam_stream.jpeg_size(a), (640, 360))

    def test_latest_frame_hands_out_only_the_newest(self):
        latest = webcam_stream.LatestFrame()
        latest.publish(b"one")
        latest.publish(b"two")
        sequence, frame = latest.wait_newer(0, timeout=0.1)
        self.assertEqual((sequence, frame), (2, b"two"))
        self.assertEqual(latest.wait_newer(2, timeout=0.05), (2, b"two"))  # nothing newer: times out

    def test_command_scales_and_drops_audio(self):
        cmd = webcam_stream.build_command("/dev/video9", "1280x720", 15, 640)
        self.assertIn("-an", cmd)
        self.assertIn("fps=15,scale=640:-2", cmd)
        self.assertEqual(cmd[-1], "pipe:1")


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
        state = _dashboard_state(RobotControlService(), None, camera, MockSensors())
        sensors = state["sensors"]
        self.assertEqual(sensors["mode"], "mock")
        self.assertEqual(sensors["lidar"]["status"], "online")
        self.assertNotIn("bins", sensors["lidar"])  # the full scan stays out of the 10 Hz state
        self.assertEqual(sensors["audio"]["threshold_dbfs"], HEARING_THRESHOLD_DBFS)

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

    def test_live_sensors_without_hardware_report_why(self):
        # On a machine without the webcam, microphone, or LiDAR, each panel goes
        # offline with the child's explanation instead of failing the dashboard.
        sensors = LiveSensors(webcam_port=0)
        sensors.webcam = SensorProcess(
            "Webcam", [sys.executable, "-m", "rescuebot.webcam_stream", "--port", "0", "--device", "/nonexistent/video"],
            "webcam_status", env={"PYTHONPATH": PACKAGE_ROOT, "PATH": "/nonexistent"},
        )
        sensors.lidar_process = SensorProcess(
            "LiDAR", [sys.executable, "-m", "rescuebot.lidar_scan", "--bin", "/nonexistent/ultra_simple"],
            "lidar_scan", env={"PYTHONPATH": PACKAGE_ROOT},
        )
        sensors.audio = SensorProcess(
            "Microphone", [sys.executable, "-m", "rescuebot.audio_level", "--device", "hw:9"],
            "audio_level", env={"PYTHONPATH": PACKAGE_ROOT, "PATH": "/nonexistent"},
        )
        sensors.start()
        self.addCleanup(sensors.close)

        def all_explained():
            status = sensors.status()
            return all(
                status[name]["status"] == "offline" and "not" in status[name]["message"]
                for name in ("webcam", "audio", "lidar")
            )

        self.assertTrue(wait_for(all_explained, timeout=10))
        status = sensors.status()
        self.assertIn("ffmpeg", status["webcam"]["message"])
        self.assertIn("arecord", status["audio"]["message"])
        self.assertIn("not found", status["lidar"]["message"])
        self.assertIsNone(sensors.lidar()["bins"])


if __name__ == "__main__":
    unittest.main()
