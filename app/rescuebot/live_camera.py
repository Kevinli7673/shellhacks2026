"""Live AI Camera backend: runs ai_camera_detect.py as an isolated child process.

The child owns the camera, runs inference on the IMX500, draws annotations, and
optionally serves the annotated MJPEG video itself (IMPLEMENTATION_PLAN.md
section 8: video is served directly from the camera service). The dashboard
process only reads detection_frame JSON lines from the child's stdout and keeps
the newest frame. This module has no dependency on control, motor, or arming
code, and a camera failure only changes the reported camera status.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Sequence

from .detection_replay import RECORD_TYPE
from .detections import DetectionFrame, DetectionTracker


DEFAULT_DETECTOR = Path(__file__).resolve().parents[2] / "ai_camera_detect.py"
DEFAULT_DETECTOR_ARGS = "--only person"
DEFAULT_VIDEO_PORT = 8081


class LiveCameraBackend:
    """Start the detector process and track the freshness of its detection frames."""

    def __init__(
        self,
        detector: str | Path = DEFAULT_DETECTOR,
        detector_args: str | Sequence[str] = DEFAULT_DETECTOR_ARGS,
        *,
        video_port: int = DEFAULT_VIDEO_PORT,
        python: str = sys.executable,
        expiry_s: float = 1.0,
    ) -> None:
        self.detector = Path(detector)
        self.detector_args = (
            shlex.split(detector_args) if isinstance(detector_args, str) else list(detector_args)
        )
        self.video_port = video_port
        self.python = python
        self._tracker = DetectionTracker(expiry_s=expiry_s)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._start_error: str | None = None
        self._invalid_lines = 0
        self._frames = 0

    def command(self) -> list[str]:
        cmd = [self.python, "-u", str(self.detector), "--json", "--headless"]
        if self.video_port:
            cmd += ["--stream-port", str(self.video_port)]
        return cmd + self.detector_args

    def start(self) -> None:
        if self._process is not None or self._start_error is not None:
            return
        if not self.detector.exists():
            self._start_error = f"Detector script not found: {self.detector}"
            return
        try:
            # Own session: a Ctrl+C in the dashboard terminal reaches only the
            # dashboard, which then stops the camera process from close().
            self._process = subprocess.Popen(
                self.command(),
                stdout=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            self._start_error = f"Camera process could not start: {exc}"
            return
        self._reader = threading.Thread(
            target=self._read, args=(self._process,), name="live-camera-reader", daemon=True
        )
        self._reader.start()

    def _read(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or record.get("type") != RECORD_TYPE:
                    raise ValueError("not a detection_frame record")
                frame = DetectionFrame.from_dict(record)
            except ValueError:  # json.JSONDecodeError is a ValueError
                with self._lock:
                    self._invalid_lines += 1
                continue
            with self._lock:
                self._tracker.update(frame, now=time.monotonic())
                self._frames += 1

    def close(self, timeout_s: float = 3.0) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.send_signal(signal.SIGINT)  # lets the detector release the camera cleanly
            process.wait(timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout_s)
        except ProcessLookupError:
            pass

    def status(self, now: float | None = None) -> dict[str, object]:
        now = time.monotonic() if now is None else now
        with self._lock:
            tracked = self._tracker.status(now=now)
            invalid = self._invalid_lines
            frames = self._frames
        process = self._process
        alive = process is not None and process.poll() is None

        if tracked.state == "online":
            message = "Live detections are current."
        elif self._start_error is not None:
            message = self._start_error
        elif process is not None and not alive:
            message = f"Camera process stopped (exit code {process.returncode})."
        elif tracked.state == "stale":
            message = "Camera results are stale; detections have expired."
        elif alive:
            message = "Starting the AI Camera. The first start can take a few minutes to load the model."
        else:
            message = "Camera process is not running."

        return {
            "backend": "live",
            "status": tracked.state,
            "message": message,
            "detection_count": len(tracked.detections),
            "detections": [detection.as_dict() for detection in tracked.detections],
            "frame": None if tracked.frame is None else tracked.frame.as_dict(),
            "age_ms": None if tracked.age_s is None else round(tracked.age_s * 1000),
            "dropped_frames": invalid,
            "frames_received": frames,
            "process_alive": alive,
            "video": (
                {"port": self.video_port, "path": "/stream.mjpg"}
                if self.video_port and alive
                else None
            ),
        }


def detector_args_from_env(default: str = DEFAULT_DETECTOR_ARGS) -> str:
    return os.environ.get("RESCUEBOT_CAMERA_ARGS", default)
