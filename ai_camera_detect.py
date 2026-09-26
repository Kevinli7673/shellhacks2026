#!/usr/bin/env python3
"""Object detection for the Raspberry Pi AI Camera (IMX500), tuned for the rescue robot.

The neural network (YOLO11n by default) runs on the camera's own AI chip, so
the Pi's CPU only reads the results and draws boxes with OpenCV.

Accuracy features:
  - Persistence filter: a label is only reported after it appears in
    --hits of the last --window inference results (default 3 of 5).
  - Per-class confidence thresholds (CLASS_THRESHOLDS, or --class-threshold).
  - Square crop for the model input, so objects aren't stretched (on by default).
  - --hazards preset: only the COCO classes useful for this robot.
  - --labels / --box-format for custom models (e.g. a fire/smoke YOLO model).

With --json, each inference result is printed as one "detection_frame" record in
the same format as app/rescuebot/detection_replay.py (IMPLEMENTATION_PLAN.md
section 8), so saved output can be replayed by the dashboard:
timestamp (capture time, seconds), frame_id, camera_id, image width/height, and
detections with label, confidence, and a bbox normalized to the displayed image
(origin top-left, x rightward, y downward, clipped to the image).

Run on the Pi (from a desktop terminal so the preview window can open):
    python3 ai_camera_detect.py --hazards
    python3 ai_camera_detect.py --only person --hits 4 --window 6
    python3 ai_camera_detect.py --headless --json   # over SSH, JSON lines to stdout
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
from collections import defaultdict, deque

MODEL_DIR = "/usr/share/imx500-models"
MODEL_CANDIDATES = [
    "imx500_network_yolo11n_pp.rpk",
    "imx500_network_yolov8n_pp.rpk",
    "imx500_network_ssd_mobilenetv2_fpnlite_320x320_pp.rpk",
]

# COCO labels indexed by original COCO id - 1; "-" marks unused ids.
# SSD MobileNet uses this list as-is. YOLO models use it with the "-" entries removed.
COCO_LABELS_91 = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "-", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "-", "backpack",
    "umbrella", "-", "-", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "-", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
    "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut",
    "cake", "chair", "couch", "potted plant", "bed", "-", "dining table", "-", "-", "toilet",
    "-", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven",
    "toaster", "sink", "refrigerator", "-", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

# COCO classes that matter for a danger-scanning robot (used by --hazards).
HAZARD_CLASSES = ["person", "knife", "scissors", "oven", "microwave", "toaster"]

# Per-class confidence thresholds. Classes not listed use --threshold.
# Starting points only: tune them from logged test runs.
CLASS_THRESHOLDS = {"person": 0.45, "knife": 0.7, "scissors": 0.7}

STALE_AFTER_S = 1.0  # plan: expire results after one second without fresh events
RECORD_TYPE = "detection_frame"  # matches app/rescuebot/detection_replay.py


def default_model():
    for name in MODEL_CANDIDATES:
        path = os.path.join(MODEL_DIR, name)
        if os.path.exists(path):
            return path
    return os.path.join(MODEL_DIR, MODEL_CANDIDATES[0])


def get_args(argv=None):
    p = argparse.ArgumentParser(description="AI Camera object detection for the rescue robot")
    p.add_argument("--model", default=default_model(), help="path to an IMX500 .rpk model (default: %(default)s)")
    p.add_argument("--labels", help="text file with one label per line, for custom models")
    p.add_argument("--box-format", choices=["auto", "yolo", "ssd"], default="auto",
                   help="box layout of the model output; auto guesses from the file name (default: %(default)s)")
    p.add_argument("--threshold", type=float, default=0.5,
                   help="minimum confidence for classes without their own threshold (default: %(default)s)")
    p.add_argument("--class-threshold", action="append", metavar="LABEL=N",
                   help='per-class threshold, repeatable, e.g. --class-threshold person=0.5 "cell phone=0.6"')
    p.add_argument("--only", nargs="+", metavar="LABEL", help="only report these labels, e.g. --only person")
    p.add_argument("--hazards", action="store_true", help=f"only report {', '.join(HAZARD_CLASSES)}")
    p.add_argument("--hits", type=int, default=3,
                   help="results a label must appear in before it's reported; 1 turns the filter off (default: %(default)s)")
    p.add_argument("--window", type=int, default=5,
                   help="how many recent results --hits counts over (default: %(default)s)")
    p.add_argument("--square-crop", action=argparse.BooleanOptionalAction, default=True,
                   help="crop the model input to a square instead of stretching it (default: on)")
    p.add_argument("--width", type=int, default=640, help="preview width (default: %(default)s)")
    p.add_argument("--height", type=int, default=480, help="preview height (default: %(default)s)")
    p.add_argument("--fps", type=float, help="camera frame rate (default: the model's inference rate)")
    p.add_argument("--headless", action="store_true", help="no preview window (use over SSH)")
    p.add_argument("--json", action="store_true", help="print one JSON detection event per inference result")
    p.add_argument("--camera-id", default="front", help="camera_id written into events (default: %(default)s)")
    args = p.parse_args(argv)
    if args.hits < 1 or args.window < 1 or args.hits > args.window:
        p.error("--hits and --window must be at least 1, and --hits can't be larger than --window")
    return args


def parse_class_thresholds(pairs, base):
    """Merge LABEL=N strings into a copy of the base thresholds."""
    thresholds = dict(base)
    for pair in pairs or []:
        label, sep, value = pair.rpartition("=")
        label = label.strip()
        if not sep or not label:
            sys.exit(f"Bad --class-threshold '{pair}': use LABEL=N, e.g. person=0.5")
        try:
            thresholds[label] = float(value)
        except ValueError:
            sys.exit(f"Bad --class-threshold '{pair}': '{value}' isn't a number")
    return thresholds


def load_labels(path):
    with open(path) as f:
        labels = [line.strip() for line in f if line.strip()]
    if not labels:
        sys.exit(f"Labels file is empty: {path}")
    return labels


def model_settings(model_path, intrinsics, labels_file=None, box_format="auto"):
    """Return (labels, normalize_boxes, box_order) for a post-processed (_pp) IMX500 model."""
    if box_format == "auto":
        box_format = "yolo" if "yolo" in os.path.basename(model_path).lower() else "ssd"
    is_yolo = box_format == "yolo"

    if labels_file:
        labels = load_labels(labels_file)
    else:
        labels = intrinsics.labels if intrinsics and intrinsics.labels else None
        if labels is None:
            labels = [l for l in COCO_LABELS_91 if l != "-"] if is_yolo else COCO_LABELS_91
        elif intrinsics.ignore_dash_labels:
            labels = [l for l in labels if l and l != "-"]

    if is_yolo:
        # Raspberry Pi's model table runs YOLO with --bbox-normalization --bbox-order xy.
        return labels, True, "xy"
    normalize = bool(intrinsics and intrinsics.bbox_normalization)
    order = (intrinsics and intrinsics.bbox_order) or "yx"
    return labels, normalize, order


def make_event(frame_id, camera_id, width, height, sensor_timestamp_ns, found, now=None):
    """Build one detection_frame record from pixel boxes, clipping boxes to the image.

    Boxes that fall completely outside the image are dropped. The timestamp is the
    sensor's capture time in seconds when available, otherwise the local monotonic clock.
    """
    if sensor_timestamp_ns:
        timestamp = sensor_timestamp_ns / 1e9
    else:
        timestamp = time.monotonic() if now is None else now
    detections = []
    for label, conf, (x, y, w, h) in found:
        x0, x1 = min(max(x, 0), width), min(max(x + w, 0), width)
        y0, y1 = min(max(y, 0), height), min(max(y + h, 0), height)
        if x1 <= x0 or y1 <= y0:
            continue
        nx0, nx1 = round(x0 / width, 4), round(x1 / width, 4)
        ny0, ny1 = round(y0 / height, 4), round(y1 / height, 4)
        detections.append({
            "label": label,
            "confidence": round(min(max(float(conf), 0.0), 1.0), 3),
            "bbox": {"x": nx0, "y": ny0, "width": round(nx1 - nx0, 4), "height": round(ny1 - ny0, 4)},
        })
    return {
        "type": RECORD_TYPE,
        "timestamp": timestamp,
        "frame_id": frame_id,
        "camera_id": camera_id,
        "image": {"width": width, "height": height},
        "detections": detections,
    }


class Confirmer:
    """Only pass a label once it has appeared in `hits` of the last `window` inference results."""

    def __init__(self, hits, window):
        self.hits = hits
        self.history = defaultdict(lambda: deque(maxlen=window))

    def __call__(self, found):
        if self.hits <= 1:
            return found
        seen = {label for label, _, _ in found}
        for label in seen | set(self.history):
            self.history[label].append(label in seen)
        return [d for d in found if sum(self.history[d[0]]) >= self.hits]


def main():
    args = get_args()
    if not os.path.exists(args.model):
        sys.exit(f"Model not found: {args.model}\nInstall models with: sudo apt install imx500-all")
    if args.labels and not os.path.exists(args.labels):
        sys.exit(f"Labels file not found: {args.labels}")

    # Imported here so the helper functions above can be tested without camera hardware.
    import cv2
    from picamera2 import MappedArray, Picamera2
    from picamera2.devices import IMX500

    # IMX500 must be created before Picamera2; this uploads the model to the camera.
    imx500 = IMX500(args.model)
    intrinsics = imx500.network_intrinsics
    if intrinsics and intrinsics.task and intrinsics.task != "object detection":
        sys.exit(f"{args.model} is a '{intrinsics.task}' model, not object detection")
    if intrinsics and intrinsics.postprocess == "nanodet":
        sys.exit(f"{args.model} needs CPU post-processing; use a model ending in _pp.rpk")

    labels, normalize, order = model_settings(args.model, intrinsics, args.labels, args.box_format)
    thresholds = parse_class_thresholds(args.class_threshold, CLASS_THRESHOLDS)
    wanted = set(args.only or []) | (set(HAZARD_CLASSES) if args.hazards else set())
    unknown = wanted - set(labels)
    if unknown:
        print(f"Warning: these labels aren't in the model and will never match: {', '.join(sorted(unknown))}",
              file=sys.stderr)
    confirm = Confirmer(args.hits, args.window)

    input_w, input_h = imx500.get_input_size()
    fps = args.fps or (intrinsics.inference_rate if intrinsics and intrinsics.inference_rate else 30.0)

    picam2 = Picamera2(imx500.camera_num)
    config = picam2.create_preview_configuration(
        main={"size": (args.width, args.height), "format": "XRGB8888"},  # BGR order, matches OpenCV
        controls={"FrameRate": fps},
        buffer_count=12,
    )

    events = queue.Queue(maxsize=30)  # bounded: drop events rather than build a backlog
    state = {"frame_id": 0, "dropped": 0, "boxes": [], "last_result": None}
    lock = threading.Lock()

    def parse(metadata):
        """Return a list of (label, conf, (x, y, w, h) pixels), or None if this frame has no result."""
        outputs = imx500.get_outputs(metadata, add_batch=True)
        if outputs is None:
            return None
        boxes, scores, classes = outputs[0][0], outputs[1][0], outputs[2][0]
        if normalize:
            boxes = boxes / input_h
        if order == "xy":
            boxes = boxes[:, [1, 0, 3, 2]]  # convert_inference_coords expects (y0, x0, y1, x1)

        found = []
        for box, score, cls in zip(boxes, scores, classes):
            idx = int(cls)
            label = labels[idx] if 0 <= idx < len(labels) else f"class_{idx}"
            if wanted and label not in wanted:
                continue
            if score < thresholds.get(label, args.threshold):
                continue
            x, y, w, h = imx500.convert_inference_coords(box, metadata, picam2)
            found.append((label, float(score), (x, y, w, h)))
        return found

    def on_frame(request):
        """Runs on the camera thread for every frame: parse, confirm, draw, and queue an event."""
        metadata = request.get_metadata()
        found = parse(metadata)
        if found is not None:
            found = confirm(found)
        now = time.monotonic()

        with lock:
            state["frame_id"] += 1
            if found is not None:
                state["boxes"] = found
                state["last_result"] = now
            elif state["last_result"] is None or now - state["last_result"] > STALE_AFTER_S:
                state["boxes"] = []
            boxes = state["boxes"]
            frame_id = state["frame_id"]

        with MappedArray(request, "main") as m:
            for label, conf, (x, y, w, h) in boxes:
                cv2.rectangle(m.array, (x, y), (x + w, y + h), (0, 255, 0, 0), 2)
                cv2.putText(m.array, f"{label} {conf:.2f}", (x + 4, max(y - 6, 14)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0, 0), 1, cv2.LINE_AA)

        if found is None:
            return
        event = make_event(frame_id, args.camera_id, args.width, args.height,
                           metadata.get("SensorTimestamp"), found)
        try:
            events.put_nowait(event)
        except queue.Full:
            with lock:
                state["dropped"] += 1

    filter_text = f"{args.hits} of {args.window}" if args.hits > 1 else "off"
    print(f"Model: {os.path.basename(args.model)}  threshold: {args.threshold}  fps: {fps:g}  "
          f"persistence: {filter_text}  square crop: {'on' if args.square_crop else 'off'}", file=sys.stderr)
    if wanted:
        print(f"Only reporting: {', '.join(sorted(wanted))}", file=sys.stderr)
    print("First run uploads the model to the camera; this can take a few minutes.", file=sys.stderr)
    imx500.show_network_fw_progress_bar()
    picam2.pre_callback = on_frame
    picam2.start(config, show_preview=not args.headless)
    if args.square_crop:
        # Crop the model's input to its own (square) shape instead of stretching the 4:3 sensor image.
        imx500.set_inference_aspect_ratio((input_w, input_h))
    print("Running. Press Ctrl+C to stop.", file=sys.stderr)

    last_summary = float("-inf")
    try:
        while True:
            try:
                event = events.get(timeout=1.0)
            except queue.Empty:
                continue
            if args.json:
                print(json.dumps(event), flush=True)
            elif time.monotonic() - last_summary >= 1.0:
                last_summary = time.monotonic()
                dets = event["detections"]
                text = ", ".join(f"{d['label']} {d['confidence']:.2f}" for d in dets) or "nothing"
                print(f"[frame {event['frame_id']}] {len(dets)} detected: {text}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        picam2.stop()
        if state["dropped"]:
            print(f"Dropped {state['dropped']} events (output was too slow).", file=sys.stderr)


if __name__ == "__main__":
    main()
