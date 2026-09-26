#!/usr/bin/env python3
"""Standalone object-detection test for the Raspberry Pi AI Camera (IMX500).

The neural network (YOLO11n by default) runs on the camera's own AI chip, so
the Pi's CPU only reads the results and draws boxes with OpenCV.

Detections use the generic event format from IMPLEMENTATION_PLAN.md section 8:
label, confidence, and a bbox normalized to the displayed image
(origin top-left, x rightward, y downward).

Run on the Pi (from a desktop terminal so the preview window can open):
    python3 ai_camera_detect.py
    python3 ai_camera_detect.py --only person
    python3 ai_camera_detect.py --headless --json   # over SSH, JSON lines to stdout
"""

import argparse
import json
import os
import queue
import sys
import threading
import time

import cv2
from picamera2 import MappedArray, Picamera2
from picamera2.devices import IMX500

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

STALE_AFTER_S = 1.0  # plan: expire results after one second without fresh events


def default_model():
    for name in MODEL_CANDIDATES:
        path = os.path.join(MODEL_DIR, name)
        if os.path.exists(path):
            return path
    return os.path.join(MODEL_DIR, MODEL_CANDIDATES[0])


def get_args():
    p = argparse.ArgumentParser(description="AI Camera object detection test")
    p.add_argument("--model", default=default_model(), help="path to an IMX500 .rpk model (default: %(default)s)")
    p.add_argument("--threshold", type=float, default=0.5, help="minimum confidence (default: %(default)s)")
    p.add_argument("--only", nargs="+", metavar="LABEL", help="only report these labels, e.g. --only person")
    p.add_argument("--width", type=int, default=640, help="preview width (default: %(default)s)")
    p.add_argument("--height", type=int, default=480, help="preview height (default: %(default)s)")
    p.add_argument("--fps", type=float, help="camera frame rate (default: the model's inference rate)")
    p.add_argument("--headless", action="store_true", help="no preview window (use over SSH)")
    p.add_argument("--json", action="store_true", help="print one JSON detection event per inference result")
    return p.parse_args()


def model_settings(model_path, intrinsics):
    """Return (labels, normalize_boxes, box_order) for a post-processed (_pp) IMX500 model."""
    is_yolo = "yolo" in os.path.basename(model_path).lower()

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


def main():
    args = get_args()
    if not os.path.exists(args.model):
        sys.exit(f"Model not found: {args.model}\nInstall models with: sudo apt install imx500-all")

    # IMX500 must be created before Picamera2; this uploads the model to the camera.
    imx500 = IMX500(args.model)
    intrinsics = imx500.network_intrinsics
    if intrinsics and intrinsics.task and intrinsics.task != "object detection":
        sys.exit(f"{args.model} is a '{intrinsics.task}' model, not object detection")
    if intrinsics and intrinsics.postprocess == "nanodet":
        sys.exit(f"{args.model} needs CPU post-processing; use a model ending in _pp.rpk")

    labels, normalize, order = model_settings(args.model, intrinsics)
    wanted = set(args.only) if args.only else None
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
            if score < args.threshold:
                continue
            idx = int(cls)
            label = labels[idx] if 0 <= idx < len(labels) else f"class_{idx}"
            if wanted and label not in wanted:
                continue
            x, y, w, h = imx500.convert_inference_coords(box, metadata, picam2)
            found.append((label, float(score), (x, y, w, h)))
        return found

    def on_frame(request):
        """Runs on the camera thread for every frame: parse, draw, and queue an event."""
        metadata = request.get_metadata()
        found = parse(metadata)
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
        event = {
            "capture_timestamp_ns": metadata.get("SensorTimestamp"),
            "frame_id": frame_id,
            "camera_id": imx500.camera_num,
            "width": args.width,
            "height": args.height,
            "detections": [
                {
                    "label": label,
                    "confidence": round(conf, 3),
                    "bbox": {
                        "x": round(x / args.width, 4),
                        "y": round(y / args.height, 4),
                        "width": round(w / args.width, 4),
                        "height": round(h / args.height, 4),
                    },
                }
                for label, conf, (x, y, w, h) in found
            ],
        }
        try:
            events.put_nowait(event)
        except queue.Full:
            with lock:
                state["dropped"] += 1

    print(f"Model: {os.path.basename(args.model)}  threshold: {args.threshold}  fps: {fps:g}", file=sys.stderr)
    print("First run uploads the model to the camera; this can take a few minutes.", file=sys.stderr)
    imx500.show_network_fw_progress_bar()
    picam2.pre_callback = on_frame
    picam2.start(config, show_preview=not args.headless)
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
