# Camera and sensors handoff

Workstream: camera and sensors (AI Camera live backend, sensor tools)
Branch: feature/camera-sensors (based on feature/dashboard-control 2276d32)
Status: ready for review by the dashboard/control integrator

This branch touches dashboard/control paths (app/rescuebot/web.py and static
files) because the user asked for the live camera to be integrated into the new
dashboard now. WORKSTREAMS.md merge gate 5 places camera work after stable real
driving; the integrator decides when to merge. LiDAR tools in tools/sensors/ are
outside the current milestone (AGENTS.md) and are not used by app/.

## Changes

1. ai_camera_detect.py (repository root)
   - --json prints `detection_frame` records identical to
     app/rescuebot/detection_replay.py: timestamp (sensor capture time, s),
     frame_id, camera_id (default "front"), image.width/height, detections.
     Boxes are clipped to the image; boxes fully outside are dropped.
     Previous output (capture_timestamp_ns, width, height) is replaced.
   - Accuracy: persistence filter (--hits/--window, default 3 of 5),
     per-class thresholds, square inference crop (--no-square-crop to disable),
     --hazards preset, --labels/--box-format for custom models.
   - --stream-port N serves the annotated preview as MJPEG at /stream.mjpg
     (<= --stream-fps, default 10; frames copied and encoded only while a
     viewer is connected; newest frame only).
2. app/rescuebot/live_camera.py (new): `LiveCameraBackend` runs
   ai_camera_detect.py as a child process (own session, SIGINT on close),
   parses stdout into DetectionFrame, tracks freshness with DetectionTracker
   (1 s expiry), and reports the same status shape as ReplayCameraBackend plus
   `video` {port, path} while the process is alive. No motor/control imports.
3. app/rescuebot/web.py: `--camera-backend live`, `--camera-args`
   (default "--only person"; env RESCUEBOT_CAMERA_ARGS), `--video-port`
   (default 8081; 0 disables; env RESCUEBOT_VIDEO_PORT).
4. Dashboard static files: `<img id="camera-video">` in the 4:3 screen shows
   the stream from the camera process's port. When video is shown, browser
   boxes are not drawn because the stream already has boxes drawn on the
   matching frame. Stale/offline slates stay above the video.
5. tools/sensors/: experimental sensor runner (AI Camera + webcam + LiDAR),
   ElevenLabs voice alerts, LiDAR offset/plot helpers. See its README.
6. tests/sensors/: event format parses as DetectionFrame, clipping/rounding
   edge cases, fused records replay via load_frames, LiveCameraBackend with a
   fake detector (online, stale, exit, missing script, close), web wiring,
   and the MJPEG streamer.

## Tests

    .venv/bin/python -m unittest discover -s tests

117 passed (3 consecutive runs) in a Linux container. Also checked: the
dashboard served by uvicorn in live mode with a fake detector reported
live/online with 1 detection, /stream.mjpg on the video port returned
multipart JPEG frames, and stopping the dashboard stopped the camera process.

2026-09-26, Raspberry Pi 5 with the AI Camera (IMX500, YOLO11n), after
merging origin/feature/dashboard-control (2276d32) into this branch:

- `python -m unittest discover -s tests`: 117 passed (2 consecutive runs).
- `rescuebot-dashboard --camera-backend live` (motor backend mock): the
  camera went from "Starting the AI Camera" to online; /api/state reported
  frames_received 735, frame age 14 ms, 0 dropped frames, process alive.
- /stream.mjpg on port 8081 returned 31 JPEG frames in 4 s (about 8 fps,
  under the 10 fps cap). `vcgencmd get_throttled` stayed 0x0.

## Coverage

- Physical: the live backend and MJPEG stream ran on the Pi with the AI
  Camera (checked through /api/state and a captured stream frame).
- Not yet physical: a person detection through the live dashboard, video and
  box alignment in a real browser, and Stop/driving with the camera running.
  The motor backend was mock, not the bridge.
- The captured frame looked rotated; check the camera mounting orientation
  before relying on detections.

## Known limitations

- Only one program can use the AI Camera: stop the live dashboard before
  running tools/sensors/rescue_sensors.py, and the reverse.
- The venv must be created with --system-site-packages so the camera child
  process can import picamera2.
- Values for --camera-args that start with a single option need `=`:
  `--camera-args=--hazards`.

## Next action

Open http://<pi>:8000 in a browser while the live dashboard runs, and confirm
the video, boxes, and detection count with a person in view, and that Stop and
driving controls respond while the camera loads and runs.
