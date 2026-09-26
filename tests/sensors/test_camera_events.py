"""Checks that the AI Camera script and sensor tools produce records the dashboard accepts."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "sensors"))

import ai_camera_detect as cam  # noqa: E402
import rescue_sensors as runner  # noqa: E402
import voice_alerts  # noqa: E402

from rescuebot.detection_replay import load_frames  # noqa: E402
from rescuebot.detections import DetectionFrame  # noqa: E402


class MakeEventTest(unittest.TestCase):
    def test_event_parses_as_detection_frame(self):
        event = cam.make_event(7, "front", 640, 480, 2_500_000_000,
                               [("person", 0.87, (128, 48, 192, 288))])
        frame = DetectionFrame.from_dict(event)
        self.assertEqual(event["type"], "detection_frame")
        self.assertEqual(frame.timestamp, 2.5)
        self.assertEqual((frame.image_width, frame.image_height), (640, 480))
        box = frame.detections[0].bbox
        self.assertEqual((box.x, box.y, box.width, box.height), (0.2, 0.1, 0.3, 0.6))

    def test_boxes_are_clipped_or_dropped(self):
        event = cam.make_event(1, "front", 640, 480, None, [
            ("person", 0.9, (-20, -10, 100, 100)),   # partly off the top-left
            ("person", 0.9, (600, 400, 100, 200)),   # partly off the bottom-right
            ("person", 0.9, (700, 100, 50, 50)),     # completely outside
        ], now=12.0)
        frame = DetectionFrame.from_dict(event)
        self.assertEqual(frame.timestamp, 12.0)
        self.assertEqual(len(frame.detections), 2)
        self.assertEqual(frame.detections[0].bbox.x, 0.0)
        right = frame.detections[1].bbox
        self.assertLessEqual(right.x + right.width, 1.0 + 1e-6)

    def test_rounding_never_pushes_box_past_edge(self):
        for width in (639, 640, 641, 1000, 1333):
            for x in range(0, width, 37):
                event = cam.make_event(1, "front", width, 480, None,
                                       [("person", 0.5, (x, 0, width - x, 480))], now=0.0)
                DetectionFrame.from_dict(event)  # raises if a box leaves the image

    def test_empty_result_is_valid(self):
        frame = DetectionFrame.from_dict(cam.make_event(3, "front", 640, 480, 1_000, []))
        self.assertEqual(frame.detections, ())

    def test_persistence_filter(self):
        confirm = cam.Confirmer(3, 5)
        person = ("person", 0.9, (0, 0, 1, 1))
        self.assertEqual([len(confirm([person])) for _ in range(3)], [0, 0, 1])


class SensorRunnerTest(unittest.TestCase):
    def test_fused_records_replay_in_dashboard(self):
        scan = [(float(a), 1200.0 if (a <= 6 or a >= 354) else 3000.0) for a in range(360)]
        event = cam.make_event(5, "front", 640, 480, 1_000_000_000,
                               [("person", 0.8, (256, 48, 128, 384))])
        fused = runner.fuse_event(event, scan, 100.0, 100.2, 66.0, 0.0)
        self.assertEqual(fused["detections"][0]["distance_m"], 1.2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "detections.jsonl"
            path.write_text(json.dumps(fused) + "\n", encoding="utf-8")
            frames = load_frames(path)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].detections[0].label, "person")

    def test_lidar_line_parsing(self):
        self.assertEqual(runner.parse_lidar_line("S  theta: 0.36 Dist: 01234.00 Q: 47 "), (0.36, 1234.0, 47))
        self.assertIsNone(runner.parse_lidar_line("SLAMTEC LIDAR S/N: ABC"))

    def test_voice_alert_text(self):
        dets = [{"label": "knife", "confidence": 0.9, "bearing_deg": 0},
                {"label": "person", "confidence": 0.7, "bearing_deg": -20}]
        self.assertEqual(voice_alerts.alert_text(dets), "Person detected on the left.")


if __name__ == "__main__":
    unittest.main()
