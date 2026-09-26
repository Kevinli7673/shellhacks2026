"""Isolated replay-camera process and newest-frame-only dashboard IPC.

This module has no dependency on control, motor, or arming code. Its child
process only turns detection recordings into frames for the dashboard process.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path
import queue
import time
from typing import Any

from .detection_replay import ReplayedFrame, load_frames, replay_frames
from .detections import DetectionTracker


class LatestFrameSlot:
    """A bounded IPC slot that discards old frames instead of making a backlog."""

    def __init__(self, channel: Any, dropped: Any) -> None:
        self._channel = channel
        self._dropped = dropped

    @property
    def dropped_count(self) -> int:
        with self._dropped.get_lock():
            return int(self._dropped.value)

    def _count_drop(self, count: int = 1) -> None:
        with self._dropped.get_lock():
            self._dropped.value += count

    def publish(self, item: Any) -> bool:
        """Publish without waiting; replace queued frames whenever possible."""
        try:
            self._channel.put_nowait(item)
            return True
        except queue.Full:
            pass

        removed = 0
        while True:
            try:
                self._channel.get_nowait()
                removed += 1
            except queue.Empty:
                break
        if removed:
            self._count_drop(removed)

        try:
            self._channel.put_nowait(item)
            return True
        except queue.Full:
            # A consumer or the multiprocessing feeder may race this retry.
            # Preserve nonblocking producer behavior and report the dropped frame.
            self._count_drop()
            return False

    def take_latest(self) -> Any | None:
        """Drain the slot so a slow dashboard consumes only the newest frame."""
        newest: Any | None = None
        while True:
            try:
                newest = self._channel.get_nowait()
            except queue.Empty:
                return newest


def _replay_worker(path: str, slot: LatestFrameSlot, stop: Any) -> None:
    """Run recording replay entirely outside the dashboard/control process."""
    try:
        frames = load_frames(path)
        for replayed in replay_frames(frames, stop=stop):
            if stop.is_set():
                return
            slot.publish(replayed)
    except (OSError, ValueError):
        # A missing or malformed recording leaves the dashboard camera offline.
        # The dashboard process and its motor-control loop remain unaffected.
        return


class MockCameraBackend:
    """The default camera status before an actual camera backend is selected."""

    def start(self) -> None:
        return

    def close(self) -> None:
        return

    def status(self, now: float | None = None) -> dict[str, object]:
        del now
        return {
            "backend": "mock",
            "status": "offline",
            "message": "Camera service has not been integrated.",
            "detection_count": 0,
            "detections": [],
            "frame": None,
            "age_ms": None,
            "dropped_frames": 0,
            "process_alive": False,
        }


class ReplayCameraBackend:
    """Read replay frames in a child process and track freshness in the parent."""

    def __init__(
        self,
        recording_path: str | Path,
        *,
        context: multiprocessing.context.BaseContext | None = None,
        expiry_s: float = 1.0,
    ) -> None:
        self.recording_path = Path(recording_path)
        self._context = context or multiprocessing.get_context()
        self._channel = self._context.Queue(maxsize=1)
        self._dropped = self._context.Value("i", 0)
        self._slot = LatestFrameSlot(self._channel, self._dropped)
        self._stop = self._context.Event()
        self._tracker = DetectionTracker(expiry_s=expiry_s)
        self._last_replayed: ReplayedFrame | None = None
        self._process: multiprocessing.Process | None = None
        self._start_error: str | None = None

    @property
    def process(self) -> multiprocessing.Process | None:
        """Expose the process for lifecycle supervision and tests."""
        return self._process

    def start(self) -> None:
        if self._process is not None or self._start_error is not None:
            return
        process = self._context.Process(
            target=_replay_worker,
            args=(str(self.recording_path), self._slot, self._stop),
            name="rescuebot-replay-camera",
            daemon=True,
        )
        try:
            process.start()
        except (OSError, RuntimeError) as exc:
            self._start_error = str(exc)
            return
        self._process = process

    def close(self, timeout_s: float = 0.250) -> None:
        self._stop.set()
        if self._process is None:
            return
        self._process.join(timeout_s)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout_s)

    def status(self, now: float | None = None) -> dict[str, object]:
        received_at = time.monotonic() if now is None else now
        newest = self._slot.take_latest()
        if isinstance(newest, ReplayedFrame):
            self._last_replayed = newest
            self._tracker.update(newest.frame, now=received_at)
        tracked = self._tracker.status(now=received_at)
        alive = self._process is not None and self._process.is_alive()

        if tracked.state == "online":
            message = "Replayed detections are current."
        elif tracked.state == "stale":
            message = "Replay data is stale; detections have expired."
        elif self._start_error is not None:
            message = "Replay camera process could not start."
        elif alive:
            message = "Waiting for the first replayed detection frame."
        else:
            message = "Replay is offline before any detection frame."

        return {
            "backend": "replay",
            "status": tracked.state,
            "message": message,
            "detection_count": len(tracked.detections),
            "detections": [detection.as_dict() for detection in tracked.detections],
            "frame": None if tracked.frame is None else tracked.frame.as_dict(),
            "replay": (
                None
                if self._last_replayed is None
                else {
                    "original_timestamp": self._last_replayed.original_timestamp,
                    "offset_s": self._last_replayed.offset_s,
                }
            ),
            "age_ms": None if tracked.age_s is None else round(tracked.age_s * 1000),
            "dropped_frames": self._slot.dropped_count,
            "process_alive": alive,
        }
