from pathlib import Path
import multiprocessing
import queue
import time
import unittest

from rescuebot.control import ManualControl
from rescuebot.detections import BoundingBox, Detection, DetectionFrame
from rescuebot.replay_camera import LatestFrameSlot, MockCameraBackend, ReplayCameraBackend
from rescuebot.service import RobotControlService
from rescuebot.web import create_app


FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "detections"


def frame(frame_id: int) -> DetectionFrame:
    return DetectionFrame(
        timestamp=float(frame_id),
        frame_id=frame_id,
        camera_id="front",
        image_width=640,
        image_height=480,
        detections=(Detection("person", 0.87, BoundingBox(0.2, 0.15, 0.3, 0.6)),),
    )


class ReplayCameraTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = multiprocessing.get_context("spawn")

    def wait_for_online(self, backend: ReplayCameraBackend) -> dict[str, object]:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            status = backend.status()
            if status["status"] == "online":
                return status
            time.sleep(0.010)
        self.fail("replay backend did not deliver a frame")

    def test_replay_process_moves_stale_fixture_from_offline_to_online_to_stale(self) -> None:
        backend = ReplayCameraBackend(
            FIXTURE_DIR / "stale_offline.jsonl",
            context=self.context,
        )
        try:
            self.assertEqual(backend.status(now=10.0)["status"], "offline")
            backend.start()
            online = self.wait_for_online(backend)
            self.assertEqual(online["backend"], "replay")
            self.assertEqual(online["detection_count"], 1)
            self.assertEqual(online["detections"][0]["bbox"]["x"], 0.2)
            self.assertEqual(online["replay"]["original_timestamp"], 4000.0)

            backend.close()
            # Drain the one-element IPC slot before advancing the tracker clock.
            latest = backend.status()
            stale = backend.status(now=time.monotonic() + 1.001)
            self.assertIn(latest["status"], ("online", "stale"))
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["detections"], [])
        finally:
            backend.close()

    def test_latest_slot_replaces_old_frames_without_blocking(self) -> None:
        channel: queue.Queue[DetectionFrame] = queue.Queue(maxsize=1)
        dropped = self.context.Value("i", 0)
        slot = LatestFrameSlot(channel, dropped)

        started = time.monotonic()
        for frame_id in range(25):
            self.assertTrue(slot.publish(frame(frame_id)))
        self.assertLess(time.monotonic() - started, 0.1)
        newest = slot.take_latest()
        self.assertIsNotNone(newest)
        assert newest is not None
        self.assertEqual(newest.frame_id, 24)
        self.assertEqual(slot.dropped_count, 24)

    def test_terminated_camera_process_cannot_delay_control_stop(self) -> None:
        backend = ReplayCameraBackend(
            FIXTURE_DIR / "stale_offline.jsonl",
            context=self.context,
        )
        try:
            backend.start()
            process = backend.process
            self.assertIsNotNone(process)
            assert process is not None
            process.terminate()
            process.join(1.0)
            self.assertFalse(process.is_alive())

            service = RobotControlService(ManualControl(pwm_ceiling=100, input_timeout_s=0.250))
            self.assertTrue(service.claim("operator"))
            self.assertTrue(service.enable("operator", now=1.0))
            self.assertTrue(service.keys("operator", ["KeyW"], now=1.01))
            service.stop("operator_stop")
            state = service.state()
            self.assertFalse(state["control"]["armed"])
            self.assertEqual(state["motor"]["wheels"], {"fl": 0, "fr": 0, "rl": 0, "rr": 0})

            later = backend.status(now=time.monotonic() + 1.001)
            self.assertIn(later["status"], ("offline", "stale"))
        finally:
            backend.close()

    def test_mock_backend_keeps_the_existing_offline_default(self) -> None:
        camera = MockCameraBackend()
        status = camera.status(now=1.0)
        self.assertEqual(status["backend"], "mock")
        self.assertEqual(status["status"], "offline")
        self.assertEqual(status["detection_count"], 0)
        self.assertEqual(status["detections"], [])

        app = create_app(camera_backend="mock")
        self.assertEqual(app.state.camera_service.status()["backend"], "mock")
        with self.assertRaisesRegex(ValueError, "replay_path"):
            create_app(camera_backend="replay")


if __name__ == "__main__":
    unittest.main()
