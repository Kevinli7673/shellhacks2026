"""Live camera backend and MJPEG streamer, tested with a fake detector process."""

import http.client
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import threading
import time
import unittest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import ai_camera_detect as cam  # noqa: E402

from rescuebot.live_camera import LiveCameraBackend  # noqa: E402
from rescuebot.service import RobotControlService  # noqa: E402
from rescuebot.web import _dashboard_state, create_app  # noqa: E402

FAKE_DETECTOR = textwrap.dedent('''
    import json, signal, sys, time
    args = sys.argv[1:]
    open(ARGS_FILE, "w").write(json.dumps(args))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    print("Model: fake", file=sys.stderr, flush=True)
    print("not json", flush=True)
    print(json.dumps({"type": "other"}), flush=True)
    frame = {"type": "detection_frame", "timestamp": 1.0, "frame_id": 0, "camera_id": "front",
             "image": {"width": 640, "height": 480},
             "detections": [{"label": "person", "confidence": 0.9,
                             "bbox": {"x": 0.2, "y": 0.1, "width": 0.3, "height": 0.6}}]}
    for i in range(COUNT):
        frame["frame_id"] = i
        print(json.dumps(frame), flush=True)
        time.sleep(0.05)
    time.sleep(EXIT_AFTER)
    sys.exit(EXIT_CODE)
''')


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class LiveCameraBackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.args_file = Path(self.tmp.name) / "args.json"

    def fake(self, count=1000, exit_after=30, exit_code=0):
        path = Path(self.tmp.name) / "fake_detector.py"
        path.write_text(
            f"ARGS_FILE = {str(self.args_file)!r}\nCOUNT = {count}\nEXIT_AFTER = {exit_after}\n"
            f"EXIT_CODE = {exit_code}\n" + FAKE_DETECTOR, encoding="utf-8")
        return path

    def test_online_with_detections_and_video(self):
        backend = LiveCameraBackend(self.fake(), "--only person", video_port=8123)
        backend.start()
        self.addCleanup(backend.close)
        self.assertTrue(wait_for(lambda: backend.status()["status"] == "online"))
        status = backend.status()
        self.assertEqual(status["backend"], "live")
        self.assertEqual(status["detection_count"], 1)
        self.assertEqual(status["detections"][0]["label"], "person")
        self.assertEqual(status["video"], {"port": 8123, "path": "/stream.mjpg"})
        self.assertEqual(status["dropped_frames"], 2)  # "not json" and the non-frame record
        args = json.loads(self.args_file.read_text())
        self.assertEqual(args, ["--json", "--headless", "--stream-port", "8123", "--only", "person"])

    def test_close_stops_process(self):
        backend = LiveCameraBackend(self.fake(), "", video_port=0)
        backend.start()
        self.assertTrue(wait_for(lambda: backend.status()["frames_received"] > 0))
        backend.close()
        status = backend.status()
        self.assertFalse(status["process_alive"])
        self.assertIsNone(status["video"])
        self.assertNotIn("--stream-port", json.loads(self.args_file.read_text()))

    def test_goes_stale_then_reports_exit(self):
        backend = LiveCameraBackend(self.fake(count=3, exit_after=0.6, exit_code=3), "",
                                    video_port=0, expiry_s=0.2)
        backend.start()
        self.addCleanup(backend.close)
        self.assertTrue(wait_for(lambda: backend.status()["status"] == "online"))
        self.assertTrue(wait_for(lambda: backend.status()["status"] == "stale"))
        self.assertEqual(backend.status()["detection_count"], 0)  # stale shows no boxes
        self.assertTrue(wait_for(lambda: not backend.status()["process_alive"]))
        self.assertIn("exit code 3", backend.status()["message"])

    def test_missing_detector_is_offline_not_an_error(self):
        backend = LiveCameraBackend(Path(self.tmp.name) / "missing.py")
        backend.start()
        backend.close()
        status = backend.status()
        self.assertEqual(status["status"], "offline")
        self.assertIn("not found", status["message"])

    def test_dashboard_state_uses_live_camera(self):
        backend = LiveCameraBackend(self.fake(), "", video_port=0)
        backend.start()
        self.addCleanup(backend.close)
        self.assertTrue(wait_for(lambda: backend.status()["status"] == "online"))
        state = _dashboard_state(RobotControlService(), None, backend)
        self.assertEqual(state["camera"]["backend"], "live")
        self.assertIn("control", state)

    def test_create_app_accepts_live(self):
        app = create_app(camera_backend="live", camera_detector=self.fake(), video_port=0)
        self.assertIsInstance(app.state.camera_service, LiveCameraBackend)
        with self.assertRaises(ValueError):
            create_app(camera_backend="webcam")


class FakeFrame:
    def __init__(self, n):
        self.n = n

    def __getitem__(self, _):
        return self

    def copy(self):
        return self


class MjpegStreamerTest(unittest.TestCase):
    def test_streams_newest_frames_only_while_watched(self):
        encoded = []

        def encode(frame):
            encoded.append(frame.n)
            return b"\xff\xd8JPEG%d\xff\xd9" % frame.n

        streamer = cam.MjpegStreamer(0, 1000, encode, host="127.0.0.1")
        self.addCleanup(streamer.close)
        streamer.offer(FakeFrame(0))  # nobody watching: ignored
        time.sleep(0.1)
        self.assertEqual(encoded, [])

        conn = http.client.HTTPConnection("127.0.0.1", streamer.port, timeout=5)
        conn.request("GET", "/stream.mjpg")
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("multipart/x-mixed-replace", response.getheader("Content-Type"))
        self.assertTrue(wait_for(lambda: streamer.clients == 1))

        stop = threading.Event()

        def feed():
            n = 1
            while not stop.is_set():
                streamer.offer(FakeFrame(n))
                n += 1
                time.sleep(0.01)

        feeder = threading.Thread(target=feed, daemon=True)
        feeder.start()
        self.addCleanup(stop.set)
        header = response.fp.readline() + response.fp.readline() + response.fp.readline()
        self.assertIn(b"--frame", header)
        response.fp.readline()  # blank line before the JPEG bytes
        body = response.fp.read(int(header.split(b"Content-Length: ")[1].split(b"\r\n")[0]))
        self.assertTrue(body.startswith(b"\xff\xd8JPEG") and body.endswith(b"\xff\xd9"))
        # A viewer that leaves is noticed on the next frame write; frames keep coming.
        response.close()
        conn.close()
        self.assertTrue(wait_for(lambda: streamer.clients == 0))
        stop.set()
        count = len(encoded)
        time.sleep(0.1)
        self.assertEqual(len(encoded), count)  # no viewers: no more encoding

        missing = http.client.HTTPConnection("127.0.0.1", streamer.port, timeout=5)
        missing.request("GET", "/nope")
        self.assertEqual(missing.getresponse().status, 404)
        missing.close()


if __name__ == "__main__":
    unittest.main()
