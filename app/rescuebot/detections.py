"""Model-independent person-detection events and freshness tracking.

Providers convert model-specific outputs into these structures. Bounding boxes
use normalized displayed-image coordinates: origin at the top-left, x rightward,
y downward, all values in [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_EXPIRY_S = 1.0

# Tolerance for floating-point rounding when a box touches the image edge.
_EDGE_TOLERANCE = 1e-6


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            value = _finite_number(getattr(self, name), f"bbox.{name}")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"bbox.{name} must be between 0 and 1")
            object.__setattr__(self, name, value)
        if self.x + self.width > 1.0 + _EDGE_TOLERANCE:
            raise ValueError("bbox extends past the right edge")
        if self.y + self.height > 1.0 + _EDGE_TOLERANCE:
            raise ValueError("bbox extends past the bottom edge")

    @classmethod
    def from_pixels(
        cls,
        left: float,
        top: float,
        width: float,
        height: float,
        image_width: int,
        image_height: int,
    ) -> "BoundingBox":
        """Normalize a pixel box, clipping it to the displayed image."""
        _positive_int(image_width, "image_width")
        _positive_int(image_height, "image_height")
        x0 = min(max(left, 0.0), image_width)
        y0 = min(max(top, 0.0), image_height)
        x1 = min(max(left + width, 0.0), image_width)
        y1 = min(max(top + height, 0.0), image_height)
        return cls(
            x=x0 / image_width,
            y=y0 / image_height,
            width=max(0.0, x1 - x0) / image_width,
            height=max(0.0, y1 - y0) / image_height,
        )

    def to_pixels(self, image_width: int, image_height: int) -> tuple[int, int, int, int]:
        """Return (left, top, width, height) in pixels for annotation."""
        return (
            round(self.x * image_width),
            round(self.y * image_height),
            round(self.width * image_width),
            round(self.height * image_height),
        )

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BoundingBox":
        return cls(x=data["x"], y=data["y"], width=data["width"], height=data["height"])


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: BoundingBox

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("label must be a non-empty string")
        confidence = _finite_number(self.confidence, "confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)
        if not isinstance(self.bbox, BoundingBox):
            raise ValueError("bbox must be a BoundingBox")

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "bbox": self.bbox.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Detection":
        return cls(
            label=data["label"],
            confidence=data["confidence"],
            bbox=BoundingBox.from_dict(data["bbox"]),
        )


@dataclass(frozen=True)
class DetectionFrame:
    """Detections for one captured frame; an empty list is a valid result."""

    timestamp: float
    frame_id: int
    camera_id: str
    image_width: int
    image_height: int
    detections: tuple[Detection, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _finite_number(self.timestamp, "timestamp"))
        if isinstance(self.frame_id, bool) or not isinstance(self.frame_id, int) or self.frame_id < 0:
            raise ValueError("frame_id must be a non-negative integer")
        if not isinstance(self.camera_id, str) or not self.camera_id:
            raise ValueError("camera_id must be a non-empty string")
        _positive_int(self.image_width, "image_width")
        _positive_int(self.image_height, "image_height")
        detections = tuple(self.detections)
        if not all(isinstance(item, Detection) for item in detections):
            raise ValueError("detections must contain Detection values")
        object.__setattr__(self, "detections", detections)

    def filtered(self, threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> "DetectionFrame":
        """Return a copy keeping detections at or above the threshold."""
        return DetectionFrame(
            timestamp=self.timestamp,
            frame_id=self.frame_id,
            camera_id=self.camera_id,
            image_width=self.image_width,
            image_height=self.image_height,
            detections=tuple(d for d in self.detections if d.confidence >= threshold),
        )

    def with_timestamp(self, timestamp: float) -> "DetectionFrame":
        return DetectionFrame(
            timestamp=timestamp,
            frame_id=self.frame_id,
            camera_id=self.camera_id,
            image_width=self.image_width,
            image_height=self.image_height,
            detections=self.detections,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "frame_id": self.frame_id,
            "camera_id": self.camera_id,
            "image": {"width": self.image_width, "height": self.image_height},
            "detections": [d.as_dict() for d in self.detections],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DetectionFrame":
        try:
            image = data["image"]
            return cls(
                timestamp=data["timestamp"],
                frame_id=data["frame_id"],
                camera_id=data["camera_id"],
                image_width=image["width"],
                image_height=image["height"],
                detections=tuple(Detection.from_dict(d) for d in data["detections"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed detection frame: {exc!r}") from exc


@dataclass(frozen=True)
class DetectionStatus:
    """What a consumer should display for one camera at a given moment.

    state is "offline" before any frame arrives, "online" while the newest
    frame is within the expiry window, and "stale" once it has expired. Stale
    or offline status never carries detections, so old boxes disappear.
    """

    state: str
    frame: DetectionFrame | None
    age_s: float | None

    @property
    def detections(self) -> tuple[Detection, ...]:
        if self.state != "online" or self.frame is None:
            return ()
        return self.frame.detections

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "age_ms": None if self.age_s is None else round(self.age_s * 1000),
            "frame": None if self.frame is None else self.frame.as_dict(),
            "detections": [d.as_dict() for d in self.detections],
        }


class DetectionTracker:
    """Keeps only the newest frame and expires it without fresh events.

    Times passed to update/status are local receipt times on one monotonic
    clock, not the frame's capture timestamp.
    """

    def __init__(self, expiry_s: float = DEFAULT_EXPIRY_S) -> None:
        if expiry_s <= 0:
            raise ValueError("expiry_s must be positive")
        self.expiry_s = expiry_s
        self._frame: DetectionFrame | None = None
        self._received_at: float | None = None

    def update(self, frame: DetectionFrame, now: float) -> None:
        self._frame = frame
        self._received_at = now

    def status(self, now: float) -> DetectionStatus:
        if self._frame is None or self._received_at is None:
            return DetectionStatus(state="offline", frame=None, age_s=None)
        age_s = max(0.0, now - self._received_at)
        state = "online" if age_s <= self.expiry_s else "stale"
        return DetectionStatus(state=state, frame=self._frame, age_s=age_s)
