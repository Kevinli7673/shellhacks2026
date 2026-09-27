"""USB webcam video for the dashboard, run as its own process.

Captures the webcam (for example the Logitech Brio) with ffmpeg, scales it down,
and serves the newest frame as MJPEG at http://<host>:<port>/stream.mjpg. Only
the newest frame is kept, so a slow viewer skips frames instead of building up
delay. The microphone is handled separately by rescuebot.audio_level.

Every second it prints a status line for the dashboard:

    {"type": "webcam_status", "fps": 14.9, "frames": 1234, "viewers": 1,
     "width": 640, "height": 360, "device": "/dev/v4l/by-id/...", "port": 8082}

    python -m rescuebot.webcam_stream
    python -m rescuebot.webcam_stream --device /dev/v4l/by-id/usb-046d_Logitech_BRIO_...-video-index0
"""

from __future__ import annotations

import argparse
import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Iterator


DEFAULT_PORT = 8082
DEFAULT_DEVICE_GLOBS = ("/dev/v4l/by-id/*Brio*video-index0", "/dev/v4l/by-id/usb-*video-index0")
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
MAX_FRAME_BYTES = 2_000_000


def split_jpegs(stream: BinaryIO, read_size: int = 65536) -> Iterator[bytes]:
    """Yield complete JPEG images from a concatenated MJPEG byte stream."""
    buffer = b""
    while True:
        data = stream.read1(read_size) if hasattr(stream, "read1") else stream.read(read_size)
        if not data:
            return
        buffer += data
        while True:
            start = buffer.find(SOI)
            if start < 0:
                buffer = buffer[-1:]  # keep a possible first half of a marker
                break
            end = buffer.find(EOI, start + 2)
            if end < 0:
                buffer = buffer[start:]
                if len(buffer) > MAX_FRAME_BYTES:
                    buffer = b""  # corrupt stream; resynchronize on the next SOI
                break
            yield buffer[start : end + 2]
            buffer = buffer[end + 2 :]


def jpeg_size(frame: bytes) -> tuple[int, int] | None:
    """Return (width, height) from a JPEG's start-of-frame segment."""
    index = 2
    while index + 9 < len(frame):
        if frame[index] != 0xFF:
            return None
        marker = frame[index + 1]
        length = struct.unpack(">H", frame[index + 2 : index + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", frame[index + 5 : index + 9])
            return width, height
        index += 2 + length
    return None


class LatestFrame:
    """The newest JPEG, handed to any number of viewers without queueing."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._frame: bytes | None = None
        self._sequence = 0
        self.viewers = 0

    def publish(self, frame: bytes) -> None:
        with self._condition:
            self._frame = frame
            self._sequence += 1
            self._condition.notify_all()

    def wait_newer(self, after: int, timeout: float) -> tuple[int, bytes | None]:
        with self._condition:
            self._condition.wait_for(lambda: self._sequence != after, timeout)
            return self._sequence, self._frame


def make_handler(latest: LatestFrame) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            if self.path.split("?")[0] != "/stream.mjpg":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            latest.viewers += 1
            sequence = 0
            try:
                while True:
                    sequence, frame = latest.wait_newer(sequence, timeout=2.0)
                    if frame is None:
                        continue
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
                    )
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                latest.viewers -= 1

    return Handler


def default_device() -> str | None:
    for pattern in DEFAULT_DEVICE_GLOBS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return None


def build_command(device: str, capture_size: str, fps: int, width: int) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "v4l2", "-input_format", "mjpeg", "-video_size", capture_size, "-framerate", "30",
        "-i", device,
        "-an", "-vf", f"fps={fps},scale={width}:-2", "-c:v", "mjpeg", "-q:v", "6",
        "-f", "mjpeg", "pipe:1",
    ]


def emit(record: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve(frames: Iterator[bytes], latest: LatestFrame, device: str, port: int) -> None:
    count = 0
    window_start = time.monotonic()
    window_frames = 0
    size: tuple[int, int] | None = None
    for frame in frames:
        latest.publish(frame)
        count += 1
        window_frames += 1
        if size is None:
            size = jpeg_size(frame)
        now = time.monotonic()
        if now - window_start >= 1.0:
            emit({
                "type": "webcam_status",
                "fps": round(window_frames / (now - window_start), 1),
                "frames": count,
                "viewers": latest.viewers,
                "width": size[0] if size else None,
                "height": size[1] if size else None,
                "device": device,
                "port": port,
            })
            window_start, window_frames = now, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve USB webcam video as MJPEG for the dashboard.")
    parser.add_argument("--device", default=os.environ.get("RESCUEBOT_WEBCAM_DEVICE"),
                        help="V4L2 device (default: the Brio, else the first USB camera under /dev/v4l/by-id)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--capture-size", default="1280x720", help="size requested from the camera")
    parser.add_argument("--width", type=int, default=640, help="width of the served video")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--stdin", action="store_true",
                        help="read concatenated JPEGs from stdin instead of ffmpeg (for testing)")
    args = parser.parse_args(argv)

    latest = LatestFrame()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(latest))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="webcam-http", daemon=True).start()

    if args.stdin:
        serve(split_jpegs(sys.stdin.buffer), latest, "stdin", args.port)
        return 0

    device = args.device or default_device()
    if device is None:
        emit({"type": "sensor_message", "message": "No USB webcam found under /dev/v4l/by-id. Check the webcam cable."})
        return 2
    if shutil.which("ffmpeg") is None:
        emit({"type": "sensor_message", "message": "ffmpeg is not installed (sudo apt install ffmpeg)."})
        return 2

    child = subprocess.Popen(
        build_command(device, args.capture_size, args.fps, args.width),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )

    def stop(_signum: int, _frame: object) -> None:
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    assert child.stdout is not None and child.stderr is not None
    try:
        serve(split_jpegs(child.stdout), latest, device, args.port)
    finally:
        if child.poll() is None:
            child.terminate()
        server.shutdown()
    code = child.wait()
    error = child.stderr.read().decode("utf-8", "replace").strip().splitlines()
    detail = error[-1][:120] if error else f"exit code {code}"
    emit({"type": "sensor_message", "message": f"Webcam capture stopped: {detail}"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
