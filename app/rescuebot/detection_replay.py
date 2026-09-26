"""JSONL recording and interval-preserving replay of detection frames.

Replay only produces detection frames. It has no path to any motor backend.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import queue
import threading
import time
from typing import Callable, Iterable, Iterator, TextIO

from .detections import DetectionFrame


RECORD_TYPE = "detection_frame"


def frame_to_record(frame: DetectionFrame) -> dict[str, object]:
    return {"type": RECORD_TYPE, **frame.as_dict()}


def encode_frame(frame: DetectionFrame) -> str:
    return json.dumps(frame_to_record(frame), separators=(",", ":"))


def parse_records(lines: Iterable[str], source: str = "<jsonl>") -> Iterator[DetectionFrame]:
    """Yield detection frames, skipping blank lines and other record types."""
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{source}:{line_number}: record must be an object")
        if record.get("type") != RECORD_TYPE:
            continue
        try:
            yield DetectionFrame.from_dict(record)
        except ValueError as exc:
            raise ValueError(f"{source}:{line_number}: {exc}") from exc


def load_frames(path: str | Path) -> list[DetectionFrame]:
    path = Path(path)
    with path.open(encoding="utf-8") as handle:
        return list(parse_records(handle, source=str(path)))


class DetectionRecorder:
    """Appends frames to a JSONL file without blocking the producer.

    submit() never waits: when the bounded queue is full the frame is dropped
    and counted in dropped_count.
    """

    def __init__(self, path: str | Path, max_pending: int = 256) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self.path = Path(path)
        self._queue: queue.Queue[DetectionFrame | None] = queue.Queue(maxsize=max_pending)
        self._dropped = 0
        self._written = 0
        self._lock = threading.Lock()
        self._closed = False
        self._stop = threading.Event()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: TextIO = self.path.open("a", encoding="utf-8")
        self._thread = threading.Thread(target=self._run, name="detection-recorder", daemon=True)
        self._thread.start()

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def written_count(self) -> int:
        with self._lock:
            return self._written

    def submit(self, frame: DetectionFrame) -> bool:
        # Serialize the closed check with enqueueing so that no frame can be
        # accepted after close has started.
        with self._lock:
            if self._closed:
                return False
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                self._dropped += 1
                return False
            return True

    def close(self, timeout_s: float = 2.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            # A full queue is normal under a slow disk. The worker observes
            # _stop after draining it, so close must never wait for room.
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        self._thread.join(timeout_s)

    def __enter__(self) -> "DetectionRecorder":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _run(self) -> None:
        try:
            while True:
                try:
                    frame = self._queue.get(timeout=0.050)
                except queue.Empty:
                    if self._stop.is_set():
                        return
                    continue
                if frame is None:
                    return
                self._handle.write(encode_frame(frame) + "\n")
                self._handle.flush()
                with self._lock:
                    self._written += 1
        finally:
            self._handle.close()


@dataclass(frozen=True)
class ReplayedFrame:
    """A frame delivered by replay.

    frame.timestamp is rebased onto the replay clock; original_timestamp is
    the capture time stored in the recording.
    """

    frame: DetectionFrame
    original_timestamp: float
    offset_s: float

    def as_dict(self) -> dict[str, object]:
        data = self.frame.as_dict()
        data["replay"] = {
            "original_timestamp": self.original_timestamp,
            "offset_s": self.offset_s,
        }
        return data


def replay_offsets(frames: Iterable[DetectionFrame]) -> list[float]:
    """Delivery offsets from the first frame; backwards timestamps deliver immediately."""
    offsets: list[float] = []
    first: float | None = None
    latest = 0.0
    for frame in frames:
        if first is None:
            first = frame.timestamp
        latest = max(latest, frame.timestamp - first)
        offsets.append(latest)
    return offsets


def replay_frames(
    frames: Iterable[DetectionFrame],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    stop: threading.Event | None = None,
) -> Iterator[ReplayedFrame]:
    """Yield frames at their recorded intervals relative to the replay start.

    Offsets are measured from the start, so slow consumers do not accumulate
    drift. Setting stop ends replay before the next delivery.
    """
    frames = list(frames)
    offsets = replay_offsets(frames)
    start = clock()
    for frame, offset in zip(frames, offsets):
        while True:
            if stop is not None and stop.is_set():
                return
            remaining = start + offset - clock()
            if remaining <= 0:
                break
            if stop is not None:
                stop.wait(remaining)
            else:
                sleep(remaining)
        yield ReplayedFrame(
            frame=frame.with_timestamp(start + offset),
            original_timestamp=frame.timestamp,
            offset_s=offset,
        )
