#!/usr/bin/env python3
"""Run the rescuebot's sensors together and save everything from one run in one folder.

  - AI Camera: runs ai_camera_detect.py (object detection on the camera's own chip)
  - Logitech webcam: records video, takes a photo every few seconds, and saves a
    snapshot whenever the AI Camera detects something
  - RPLIDAR C1: records every 360-degree scan, and gives each AI detection a
    distance by looking up the lidar points at the same angle (sensor fusion)

Each program runs as its own process, so one failing doesn't take down the others.

Usage (on the Pi):
    python3 rescue_sensors.py                  # run until Ctrl+C
    python3 rescue_sensors.py --duration 60    # stop after 60 seconds
    python3 rescue_sensors.py --preview        # also show the AI Camera window
    python3 rescue_sensors.py --voice          # speak alerts with ElevenLabs (voice_alerts.py)

Output folder (~/rescuebot_runs/<date_time>/):
    video.mkv            webcam recording
    photos/              a webcam photo every --photo-every seconds
    snapshots/           webcam photo + JSON each time the AI Camera detects something
    detections.jsonl     every AI Camera result, with bearing and lidar distance added
    lidar.jsonl          lidar scans: [angle_deg, distance_mm] pairs
"""

import argparse
import glob
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# ai_camera_detect.py lives at the repository root; a copy next to this script also works.
DEFAULT_DETECTOR = next(
    (p for p in (os.path.join(HERE, "ai_camera_detect.py"),
                 os.path.join(HERE, "..", "..", "ai_camera_detect.py")) if os.path.exists(p)),
    os.path.join(HERE, "..", "..", "ai_camera_detect.py"))

# Output line from Slamtec's ultra_simple, e.g. "S  theta: 12.34 Dist: 01234.00 Q: 47 "
LIDAR_LINE = re.compile(r"theta:\s*([0-9.]+)\s+Dist:\s*([0-9.]+)\s+Q:\s*(\d+)")

AI_CAMERA_HFOV_DEG = 66.0  # Raspberry Pi AI Camera horizontal field of view (spec: 66 +/- 3 degrees)


def first_glob(pattern, fallback=None):
    matches = sorted(glob.glob(os.path.expanduser(pattern)))
    return matches[0] if matches else fallback


def get_args(argv=None):
    p = argparse.ArgumentParser(description="Run the AI Camera, webcam, and lidar together")
    p.add_argument("--duration", type=float, help="stop after this many seconds (default: run until Ctrl+C)")
    p.add_argument("--out", default="~/rescuebot_runs", help="parent folder for run folders (default: %(default)s)")

    g = p.add_argument_group("AI Camera")
    g.add_argument("--no-ai", action="store_true", help="don't run the AI Camera")
    g.add_argument("--detector", default=os.path.normpath(DEFAULT_DETECTOR),
                   help="path to ai_camera_detect.py (default: %(default)s)")
    g.add_argument("--detector-args", default="--hazards",
                   help='extra options for ai_camera_detect.py, in quotes (default: "%(default)s")')
    g.add_argument("--preview", action="store_true", help="show the AI Camera preview window (needs the desktop)")
    g.add_argument("--snapshot-cooldown", type=float, default=3.0,
                   help="minimum seconds between detection snapshots (default: %(default)s)")

    g = p.add_argument_group("Webcam")
    g.add_argument("--no-webcam", action="store_true", help="don't use the webcam")
    g.add_argument("--cam", default=first_glob("/dev/v4l/by-id/*Brio_101*video-index0"),
                   help="webcam device (default: the Brio 101 by-id path)")
    g.add_argument("--video-size", default="1920x1080", help="recording size (default: %(default)s)")
    g.add_argument("--photo-every", type=float, default=2.0,
                   help="seconds between automatic photos; 0 turns them off (default: %(default)s)")

    g = p.add_argument_group("Lidar")
    g.add_argument("--no-lidar", action="store_true", help="don't use the lidar")
    g.add_argument("--lidar-bin", default="~/rplidar_sdk/output/Linux/Release/ultra_simple",
                   help="path to Slamtec's ultra_simple program (default: %(default)s)")
    g.add_argument("--lidar-port", default=first_glob("/dev/serial/by-id/*CP2102N*", "/dev/ttyUSB0"),
                   help="lidar serial port (default: the CP2102N by-id path)")
    g.add_argument("--lidar-baud", type=int, default=460800, help="RPLIDAR C1 baud rate (default: %(default)s)")
    g.add_argument("--lidar-offset", type=float, default=0.0,
                   help="degrees to add to camera angles to get lidar angles; calibrate if the "
                        "lidar's 0 degrees doesn't point where the AI Camera points (default: %(default)s)")
    g.add_argument("--lidar-log-every", type=int, default=2,
                   help="save every Nth scan to lidar.jsonl (default: %(default)s)")
    g.add_argument("--hfov", type=float, default=AI_CAMERA_HFOV_DEG,
                   help="AI Camera horizontal field of view in degrees (default: %(default)s)")

    g = p.add_argument_group("Voice alerts")
    g.add_argument("--voice", action="store_true",
                   help="speak detections out loud with ElevenLabs (needs ELEVENLABS_API_KEY and a speaker)")
    g.add_argument("--voice-cooldown", type=float, default=8.0,
                   help="seconds before the same sentence can be spoken again (default: %(default)s)")
    g.add_argument("--say-distance", action="store_true",
                   help="include the lidar distance in spoken alerts (only once --lidar-offset is calibrated)")

    args = p.parse_args(argv)
    args.out = os.path.expanduser(args.out)
    args.detector = os.path.expanduser(args.detector)
    args.lidar_bin = os.path.expanduser(args.lidar_bin)
    if args.lidar_log_every < 1:
        p.error("--lidar-log-every must be at least 1")
    return args


# ---------------------------------------------------------------- lidar helpers

def parse_lidar_line(line):
    """Return (theta_deg, dist_mm, quality) from an ultra_simple line, or None for other lines."""
    m = LIDAR_LINE.search(line)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), int(m.group(3))


class ScanAssembler:
    """Group ultra_simple points into full 360-degree scans.

    ultra_simple prints each scan in rising angle order, so a new scan starts
    when the angle jumps back down. Points with distance 0 or quality 0 are invalid and dropped.
    """

    def __init__(self):
        self.points = []
        self.last_theta = None

    def add(self, theta, dist, quality):
        """Add one point. Returns the finished scan's points when a new scan begins, else None."""
        done = None
        if self.last_theta is not None and theta < self.last_theta - 180 and self.points:
            done, self.points = self.points, []
        self.last_theta = theta
        if dist > 0 and quality > 0:
            self.points.append((theta, dist))
        return done


# ---------------------------------------------------------------- fusion helpers

def x_to_bearing(x_norm, hfov_deg):
    """Horizontal position in the image (0 = left edge, 1 = right edge) -> degrees, right is positive."""
    half_width = math.tan(math.radians(hfov_deg / 2))
    return math.degrees(math.atan((x_norm - 0.5) * 2 * half_width))


def angle_in_range(a, lo, hi):
    """True if angle a is between lo and hi going clockwise, handling the 0/360 wrap."""
    a, lo, hi = a % 360, lo % 360, hi % 360
    return lo <= a <= hi if lo <= hi else (a >= lo or a <= hi)


def distance_for_bbox(scan_points, bbox, hfov_deg, offset_deg, min_half_width_deg=1.5):
    """Return (bearing_deg, distance_m or None, points_used) for a normalized bbox.

    The RPLIDAR measures angles clockwise, so an object right of the camera's center
    has a larger lidar angle. The lower quartile of the matching distances is used,
    which favors the object itself over the background seen around its edges.
    """
    left = x_to_bearing(bbox["x"], hfov_deg)
    right = x_to_bearing(bbox["x"] + bbox["width"], hfov_deg)
    center = x_to_bearing(bbox["x"] + bbox["width"] / 2, hfov_deg)
    if right - left < 2 * min_half_width_deg:  # very narrow box: widen so it catches some lidar points
        left, right = center - min_half_width_deg, center + min_half_width_deg

    lo, hi = left + offset_deg, right + offset_deg
    dists = sorted(d for a, d in scan_points if angle_in_range(a, lo, hi))
    if not dists:
        return center, None, 0
    return center, dists[len(dists) // 4] / 1000.0, len(dists)


def fuse_event(event, scan, scan_time, now, hfov_deg, offset_deg):
    """Return a copy of an AI Camera event with bearing and lidar distance added to each detection."""
    fused = dict(event)
    fused["received_time"] = round(now, 3)
    fused["lidar_age_s"] = round(now - scan_time, 3) if scan is not None else None
    detections = []
    for det in event.get("detections", []):
        det = dict(det)
        if scan is not None:
            bearing, dist, used = distance_for_bbox(scan, det["bbox"], hfov_deg, offset_deg)
        else:
            bearing, dist, used = x_to_bearing(det["bbox"]["x"] + det["bbox"]["width"] / 2, hfov_deg), None, 0
        det["bearing_deg"] = round(bearing, 1)
        det["distance_m"] = round(dist, 2) if dist is not None else None
        det["lidar_points"] = used
        detections.append(det)
    fused["detections"] = detections
    return fused


def describe(det):
    side = "ahead" if abs(det["bearing_deg"]) < 3 else (
        f"{abs(det['bearing_deg']):.0f} deg {'right' if det['bearing_deg'] > 0 else 'left'}")
    dist = f"{det['distance_m']:.2f} m" if det["distance_m"] is not None else "distance unknown"
    return f"{det['label']} {det['confidence']:.2f} at {dist}, {side}"


# ---------------------------------------------------------------- commands

def build_detector_cmd(args):
    cmd = [sys.executable, "-u", args.detector, "--json"]
    if not args.preview:
        cmd.append("--headless")
    return cmd + shlex.split(args.detector_args)


def build_ffmpeg_cmd(args, run_dir):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "v4l2", "-input_format", "mjpeg", "-video_size", args.video_size, "-framerate", "30",
           "-i", args.cam,
           # 1) the recording, saved as-is (no re-encoding)
           "-map", "0:v", "-c:v", "copy", os.path.join(run_dir, "video.mkv")]
    if args.photo_every > 0:
        # 2) a photo every N seconds
        cmd += ["-map", "0:v", "-vf", f"fps=1/{args.photo_every:g}", "-q:v", "2",
                os.path.join(run_dir, "photos", "photo_%04d.jpg")]
    # 3) latest.jpg, overwritten 5 times a second; renamed into place so a copy is never half-written
    cmd += ["-map", "0:v", "-vf", "fps=5", "-q:v", "3", "-update", "1", "-atomic_writing", "1",
            os.path.join(run_dir, "latest.jpg")]
    return cmd


def build_lidar_cmd(args):
    # stdbuf makes ultra_simple send each line right away instead of in large chunks
    return ["stdbuf", "-oL", args.lidar_bin, "--channel", "--serial", args.lidar_port, str(args.lidar_baud)]


# ---------------------------------------------------------------- runner

def main(argv=None):
    args = get_args(argv)
    use_ai, use_cam, use_lidar = not args.no_ai, not args.no_webcam, not args.no_lidar

    problems = []
    if use_ai and not os.path.exists(args.detector):
        problems.append(f"AI Camera script not found: {args.detector} (use --detector PATH or --no-ai)")
    if use_cam and not args.cam:
        problems.append("Webcam not found under /dev/v4l/by-id/ (use --cam PATH or --no-webcam)")
    if use_lidar and not os.path.exists(args.lidar_bin):
        problems.append(f"Lidar program not found: {args.lidar_bin} (build Slamtec's SDK, or use --no-lidar)")
    if problems:
        sys.exit("\n".join(problems))

    speaker = None
    if args.voice:
        import voice_alerts  # voice_alerts.py, in the same folder as this script
        if not os.environ.get("ELEVENLABS_API_KEY"):
            print("Warning: ELEVENLABS_API_KEY isn't set; only phrases already cached can be spoken.",
                  file=sys.stderr, flush=True)
        speaker = voice_alerts.Speaker(cooldown=args.voice_cooldown)
        startup = "Rescue bot online. Scanning for hazards."
        speaker.say(startup)
        speaker.prepare([voice_alerts.alert_text([{"label": "person", "bearing_deg": b}]) for b in (0, 20, -20)])

    run_dir = os.path.join(args.out, datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.join(run_dir, "photos"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "snapshots"), exist_ok=True)
    print(f"Saving to {run_dir}", flush=True)

    stop = threading.Event()
    lock = threading.Lock()
    shared = {"scan": None, "scan_time": 0.0}
    stats = {"events": 0, "with_detections": 0, "snapshots": 0, "scans": 0}
    procs = {}

    def start(name, cmd, **kw):
        # start_new_session: Ctrl+C reaches only this script, which then stops each program cleanly
        procs[name] = subprocess.Popen(cmd, start_new_session=True, text=True, bufsize=1, **kw)

    # ---- lidar
    def lidar_reader(proc):
        assembler = ScanAssembler()
        with open(os.path.join(run_dir, "lidar.jsonl"), "w", buffering=1) as log:
            for line in proc.stdout:
                point = parse_lidar_line(line)
                if point is None:
                    if line.strip():
                        print(f"[lidar] {line.rstrip()}", flush=True)
                    continue
                scan = assembler.add(*point)
                if scan is None:
                    continue
                now = time.time()
                with lock:
                    shared["scan"], shared["scan_time"] = scan, now
                    stats["scans"] += 1
                    n = stats["scans"]
                if n == 1:
                    print(f"[lidar] first full scan: {len(scan)} points", flush=True)
                if n % args.lidar_log_every == 0:
                    log.write(json.dumps({"time": round(now, 3),
                                          "points": [[round(a, 2), round(d)] for a, d in scan]}) + "\n")

    # ---- AI Camera
    def detector_reader(proc):
        last_snapshot = float("-inf")
        last_print = float("-inf")
        latest = os.path.join(run_dir, "latest.jpg")
        with open(os.path.join(run_dir, "detections.jsonl"), "w", buffering=1) as log:
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    if line.strip():
                        print(f"[ai] {line.rstrip()}", flush=True)
                    continue
                now = time.time()
                with lock:
                    scan, scan_time = shared["scan"], shared["scan_time"]
                    stats["events"] += 1
                fused = fuse_event(event, scan, scan_time, now, args.hfov, args.lidar_offset)
                log.write(json.dumps(fused) + "\n")
                if not fused["detections"]:
                    continue
                with lock:
                    stats["with_detections"] += 1

                if now - last_print >= 1.0:
                    last_print = now
                    print("[ai] " + "; ".join(describe(d) for d in fused["detections"]), flush=True)

                if speaker:
                    speaker.say(voice_alerts.alert_text(fused["detections"], args.say_distance))

                if use_cam and now - last_snapshot >= args.snapshot_cooldown and os.path.exists(latest):
                    last_snapshot = now
                    labels = "-".join(sorted({d["label"].replace(" ", "_") for d in fused["detections"]}))
                    base = os.path.join(run_dir, "snapshots",
                                        f"{datetime.now().strftime('%H%M%S')}_{fused['frame_id']}_{labels}")
                    try:
                        shutil.copy2(latest, base + ".jpg")
                        with open(base + ".json", "w") as f:
                            json.dump(fused, f, indent=2)
                        with lock:
                            stats["snapshots"] += 1
                    except OSError as e:
                        print(f"[snapshot] failed: {e}", file=sys.stderr, flush=True)

    # ---- webcam errors, minus the harmless Logitech metadata message
    def ffmpeg_errors(proc):
        for line in proc.stderr:
            if line.strip() and "APP fields" not in line and "Last message repeated" not in line:
                print(f"[webcam] {line.rstrip()}", file=sys.stderr, flush=True)

    readers = []
    try:
        if use_lidar:
            start("lidar", build_lidar_cmd(args), stdout=subprocess.PIPE)
            readers.append(threading.Thread(target=lidar_reader, args=(procs["lidar"],), daemon=True))
        if use_cam:
            start("webcam", build_ffmpeg_cmd(args, run_dir), stdin=subprocess.DEVNULL, stderr=subprocess.PIPE)
            readers.append(threading.Thread(target=ffmpeg_errors, args=(procs["webcam"],), daemon=True))
        if use_ai:
            start("ai", build_detector_cmd(args), stdout=subprocess.PIPE)
            readers.append(threading.Thread(target=detector_reader, args=(procs["ai"],), daemon=True))
    except FileNotFoundError as e:
        for proc in procs.values():
            proc.kill()
        sys.exit(f"Couldn't start a program: {e}")
    for t in readers:
        t.start()

    def request_stop(*_):
        stop.set()
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    running = ", ".join(procs)
    print(f"Running: {running}. Press Ctrl+C to stop."
          + (f" Stopping after {args.duration:g} s." if args.duration else ""), flush=True)
    if use_ai:
        print("(The AI Camera can take a few minutes to load its model on the first run.)", flush=True)

    started = time.monotonic()
    reported = set()
    while not stop.is_set():
        if args.duration and time.monotonic() - started >= args.duration:
            break
        for name, proc in procs.items():
            if proc.poll() is not None and name not in reported:
                reported.add(name)
                print(f"Warning: {name} stopped early (exit code {proc.returncode}); "
                      f"the other sensors keep running.", file=sys.stderr, flush=True)
        stop.wait(0.5)

    print("\nStopping...", flush=True)
    for proc in procs.values():
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)  # lets ffmpeg finish the file and the lidar stop its motor
    for name, proc in procs.items():
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            print(f"{name} didn't stop in time; killing it.", file=sys.stderr, flush=True)
            proc.kill()
            proc.wait()
    for t in readers:
        t.join(timeout=2)
    if speaker:
        speaker.close()

    photos = len(glob.glob(os.path.join(run_dir, "photos", "*.jpg")))
    print(f"\nDone. Saved to {run_dir}")
    if use_ai:
        print(f"  AI Camera: {stats['events']} results, {stats['with_detections']} with detections, "
              f"{stats['snapshots']} snapshots")
    if use_cam:
        print(f"  Webcam: video.mkv, {photos} photos")
    if use_lidar:
        print(f"  Lidar: {stats['scans']} scans")


if __name__ == "__main__":
    main()
