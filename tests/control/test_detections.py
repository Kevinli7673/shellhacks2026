import json
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest

from rescuebot.detection_replay import (
    DetectionRecorder,
    encode_frame,
    load_frames,
    parse_records,
    replay_frames,
    replay_offsets,
)
from rescuebot.detections import (
    BoundingBox,
    Detection,
    DetectionFrame,
    DetectionTracker,
)


FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "detections"


def person(confidence: float = 0.87, x: float = 0.2) -> Detection:
    return Detection("person", confidence, BoundingBox(x, 0.15, 0.3, 0.6))


def frame(timestamp: float, frame_id: int, *detections: Detection) -> DetectionFrame:
    return DetectionFrame(timestamp, frame_id, "front", 640, 480, detections)


class FakeClock:
    def __init__(self, now: float = 50.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class DetectionShapeTests(unittest.TestCase):
    def test_frame_round_trips_through_the_approved_shape(self) -> None:
        original = frame(12.5, 7, person())
        data = original.as_dict()
        self.assertEqual(
            data,
            {
                "timestamp": 12.5,
                "frame_id": 7,
                "camera_id": "front",
                "image": {"width": 640, "height": 480},
                "detections": [
                    {
                        "label": "person",
                        "confidence": 0.87,
                        "bbox": {"x": 0.2, "y": 0.15, "width": 0.3, "height": 0.6},
                    }
                ],
            },
        )
        self.assertEqual(DetectionFrame.from_dict(json.loads(json.dumps(data))), original)

    def test_invalid_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BoundingBox(-0.1, 0.0, 0.5, 0.5)
        with self.assertRaises(ValueError):
            BoundingBox(0.8, 0.0, 0.3, 0.5)
        with self.assertRaises(ValueError):
            BoundingBox(0.0, 0.0, math.nan, 0.5)
        with self.assertRaises(ValueError):
            Detection("person", 1.2, BoundingBox(0, 0, 1, 1))
        with self.assertRaises(ValueError):
            Detection("", 0.5, BoundingBox(0, 0, 1, 1))
        with self.assertRaises(ValueError):
            DetectionFrame(1.0, -1, "front", 640, 480)
        with self.assertRaises(ValueError):
            DetectionFrame(1.0, 0, "front", 0, 480)
        with self.assertRaises(ValueError):
            DetectionFrame.from_dict({"timestamp": 1.0, "frame_id": 0})

    def test_pixel_conversion_uses_top_left_origin_and_clips_to_the_image(self) -> None:
        box = BoundingBox.from_pixels(128, 72, 192, 288, 640, 480)
        self.assertAlmostEqual(box.x, 0.2)
        self.assertAlmostEqual(box.y, 0.15)
        self.assertAlmostEqual(box.width, 0.3)
        self.assertAlmostEqual(box.height, 0.6)
        self.assertEqual(box.to_pixels(640, 480), (128, 72, 192, 288))

        clipped = BoundingBox.from_pixels(-40, 400, 100, 200, 640, 480)
        self.assertEqual(clipped.x, 0.0)
        self.assertAlmostEqual(clipped.width, 60 / 640)
        self.assertAlmostEqual(clipped.y + clipped.height, 1.0)

    def test_confidence_filter_keeps_threshold_and_empty_frames(self) -> None:
        filtered = frame(1.0, 0, person(0.5), person(0.49)).filtered()
        self.assertEqual([d.confidence for d in filtered.detections], [0.5])
        self.assertEqual(frame(1.0, 1).filtered().detections, ())


class DetectionTrackerTests(unittest.TestCase):
    def test_offline_before_any_frame(self) -> None:
        status = DetectionTracker().status(now=10.0)
        self.assertEqual(status.state, "offline")
        self.assertEqual(status.detections, ())

    def test_valid_empty_result_is_online_not_stale(self) -> None:
        tracker = DetectionTracker()
        tracker.update(frame(1.0, 0), now=10.0)
        status = tracker.status(now=10.5)
        self.assertEqual(status.state, "online")
        self.assertEqual(status.detections, ())

    def test_detections_expire_after_one_second_without_fresh_frames(self) -> None:
        tracker = DetectionTracker()
        tracker.update(frame(1.0, 0, person()), now=10.0)
        self.assertEqual(len(tracker.status(now=11.0).detections), 1)
        stale = tracker.status(now=11.001)
        self.assertEqual(stale.state, "stale")
        self.assertEqual(stale.detections, ())
        self.assertEqual(stale.as_dict()["detections"], [])


class RecordingTests(unittest.TestCase):
    def test_recorder_writes_jsonl_that_loads_back(self) -> None:
        frames = [frame(1.0, 0), frame(1.1, 1, person())]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run" / "detections.jsonl"
            with DetectionRecorder(path) as recorder:
                for item in frames:
                    self.assertTrue(recorder.submit(item))
            self.assertEqual(recorder.written_count, 2)
            self.assertEqual(recorder.dropped_count, 0)
            self.assertEqual(load_frames(path), frames)
            self.assertFalse(recorder.submit(frames[0]))

    def test_full_queue_drops_and_counts_instead_of_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = DetectionRecorder(Path(tmp) / "d.jsonl", max_pending=1)
            # Stall the writer so the queue stays full.
            gate = threading.Event()
            original_write = recorder._handle.write

            def blocked_write(text: str) -> int:
                gate.wait(2.0)
                return original_write(text)

            recorder._handle.write = blocked_write  # type: ignore[method-assign]
            accepted = [recorder.submit(frame(float(i), i)) for i in range(20)]
            self.assertIn(False, accepted)
            self.assertEqual(recorder.dropped_count, accepted.count(False))
            gate.set()
            recorder.close()
            self.assertEqual(recorder.written_count, accepted.count(True))

    def test_close_never_waits_for_a_full_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = DetectionRecorder(Path(tmp) / "d.jsonl", max_pending=1)
            gate = threading.Event()
            writing = threading.Event()
            original_write = recorder._handle.write

            def blocked_write(text: str) -> int:
                writing.set()
                gate.wait(2.0)
                return original_write(text)

            recorder._handle.write = blocked_write  # type: ignore[method-assign]
            self.assertTrue(recorder.submit(frame(1.0, 1)))
            self.assertTrue(writing.wait(0.5))
            self.assertTrue(recorder.submit(frame(1.1, 2)))

            started = time.monotonic()
            recorder.close(timeout_s=0.01)
            self.assertLess(time.monotonic() - started, 0.1)
            self.assertFalse(recorder.submit(frame(1.2, 3)))
            gate.set()
            recorder._thread.join(1.0)
            self.assertFalse(recorder._thread.is_alive())

    def test_parser_skips_other_record_types_and_reports_bad_lines(self) -> None:
        lines = [
            json.dumps({"type": "safety_state", "armed": False}),
            "",
            encode_frame(frame(2.0, 3)),
        ]
        self.assertEqual(list(parse_records(lines)), [frame(2.0, 3)])
        with self.assertRaisesRegex(ValueError, ":1: invalid JSON"):
            list(parse_records(["{not json"]))
        with self.assertRaisesRegex(ValueError, ":2:"):
            list(parse_records(["", '{"type": "detection_frame", "frame_id": 1}']))


class ReplayTests(unittest.TestCase):
    def replay(self, frames: list[DetectionFrame], clock: FakeClock) -> list:
        return list(replay_frames(frames, clock=clock, sleep=clock.sleep))

    def test_replay_preserves_intervals_and_original_timestamps(self) -> None:
        frames = load_frames(FIXTURE_DIR / "person_appears_disappears.jsonl")
        clock = FakeClock(now=50.0)
        delivered = []
        for item in replay_frames(frames, clock=clock, sleep=clock.sleep):
            delivered.append((clock.now, item))

        self.assertEqual(len(delivered), len(frames))
        for (delivered_at, item), source in zip(delivered, frames):
            self.assertEqual(item.original_timestamp, source.timestamp)
            self.assertAlmostEqual(item.frame.timestamp, 50.0 + source.timestamp - 1000.0)
            self.assertAlmostEqual(delivered_at, item.frame.timestamp)
            self.assertEqual(item.frame.frame_id, source.frame_id)
            self.assertEqual(item.frame.detections, source.detections)
        self.assertEqual(delivered[0][1].as_dict()["replay"]["original_timestamp"], 1000.0)

    def test_replay_does_not_accumulate_drift_for_slow_consumers(self) -> None:
        frames = [frame(0.0, 0), frame(0.1, 1), frame(0.2, 2), frame(0.3, 3)]
        clock = FakeClock(now=0.0)
        delivered_at = []
        for _item in replay_frames(frames, clock=clock, sleep=clock.sleep):
            delivered_at.append(clock.now)
            clock.now += 0.15  # consumer slower than the frame interval
        self.assertEqual(delivered_at[0], 0.0)
        self.assertAlmostEqual(delivered_at[1], 0.15)
        self.assertAlmostEqual(delivered_at[2], 0.30)
        self.assertAlmostEqual(delivered_at[3], 0.45)

    def test_backwards_timestamps_deliver_immediately(self) -> None:
        offsets = replay_offsets([frame(5.0, 0), frame(5.2, 1), frame(5.1, 2), frame(5.4, 3)])
        for actual, expected in zip(offsets, [0.0, 0.2, 0.2, 0.4]):
            self.assertAlmostEqual(actual, expected)

    def test_stop_event_ends_replay(self) -> None:
        stop = threading.Event()
        frames = [frame(0.0, 0), frame(10.0, 1)]
        replayed = []
        for item in replay_frames(frames, stop=stop):
            replayed.append(item)
            stop.set()
        self.assertEqual(len(replayed), 1)


class DetectionFixtureTests(unittest.TestCase):
    def status_timeline(self, name: str, probe_after_s: float = 0.0) -> list[tuple[str, int]]:
        """Replay a fixture into a tracker and sample status at each delivery."""
        clock = FakeClock(now=100.0)
        tracker = DetectionTracker()
        timeline = []
        last_probe = clock.now
        for item in replay_frames(load_frames(FIXTURE_DIR / name), clock=clock, sleep=clock.sleep):
            # Sample just before this delivery to observe gaps in the stream.
            before = tracker.status(now=clock.now)
            if clock.now - last_probe > tracker.expiry_s:
                timeline.append((before.state, len(before.detections)))
            tracker.update(item.frame, now=clock.now)
            status = tracker.status(now=clock.now)
            timeline.append((status.state, len(status.detections)))
            last_probe = clock.now
        if probe_after_s:
            status = tracker.status(now=clock.now + probe_after_s)
            timeline.append((status.state, len(status.detections)))
        return timeline

    def test_all_fixtures_parse(self) -> None:
        names = sorted(p.name for p in FIXTURE_DIR.glob("*.jsonl"))
        self.assertEqual(
            names,
            [
                "empty_frames.jsonl",
                "overlapping_people.jsonl",
                "person_appears_disappears.jsonl",
                "stale_offline.jsonl",
            ],
        )
        for name in names:
            with self.subTest(fixture=name):
                self.assertTrue(load_frames(FIXTURE_DIR / name))

    def test_person_appearance_and_disappearance(self) -> None:
        self.assertEqual(
            self.status_timeline("person_appears_disappears.jsonl"),
            [("online", 0), ("online", 0), ("online", 1), ("online", 1), ("online", 0), ("online", 0)],
        )

    def test_empty_frames_stay_online(self) -> None:
        self.assertEqual(
            self.status_timeline("empty_frames.jsonl"),
            [("online", 0)] * 3,
        )

    def test_overlapping_people_are_both_reported(self) -> None:
        frames = load_frames(FIXTURE_DIR / "overlapping_people.jsonl")
        for item in frames:
            first, second = (d.bbox for d in item.detections)
            overlap_w = min(first.x + first.width, second.x + second.width) - max(first.x, second.x)
            overlap_h = min(first.y + first.height, second.y + second.height) - max(first.y, second.y)
            self.assertGreater(overlap_w, 0)
            self.assertGreater(overlap_h, 0)
        self.assertEqual(self.status_timeline("overlapping_people.jsonl"), [("online", 2)] * 2)

    def test_gap_goes_stale_then_recovers_and_end_of_stream_goes_stale(self) -> None:
        self.assertEqual(
            self.status_timeline("stale_offline.jsonl", probe_after_s=1.5),
            [
                ("online", 1),
                ("online", 1),
                ("online", 1),
                ("stale", 0),  # 1.6 s gap: stale boxes disappear
                ("online", 0),
                ("online", 0),
                ("stale", 0),  # recording ended; no fresh frames
            ],
        )


if __name__ == "__main__":
    unittest.main()
