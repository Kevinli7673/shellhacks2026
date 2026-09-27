# Sensor runner tools (experimental, outside the current milestone)

These scripts run the AI Camera, the Logitech webcam, and the RPLIDAR C1 together
and save each run to `~/rescuebot_runs/<date_time>/`. They are standalone tools:
nothing in `app/` imports them, and they never touch motor control.

LiDAR use is outside the current milestone in AGENTS.md ("No LiDAR ... before
physical acceptance"). Keep these tools separate from the dashboard until the team
agrees to bring them in.

| File | Purpose |
|---|---|
| `rescue_sensors.py` | Runs `../../ai_camera_detect.py`, records webcam video and photos, records lidar scans, and adds a bearing and lidar distance to each detection. |
| `voice_alerts.py` | Shim for `app/rescuebot/voice.py`: ElevenLabs spoken alerts (`rescue_sensors.py --voice`), cached as MP3s in `~/.cache/rescuebot_voice/`. |
| `find_offset.py` | Finds `--lidar-offset` by comparing a run without a person in front of the camera to one with a person 1 m ahead. |
| `plot_scan.py` | Plots the last lidar scan of a run with the AI Camera's view shaded. |

## Setup on the Pi

    sudo apt install -y ffmpeg v4l-utils
    git clone https://github.com/Slamtec/rplidar_sdk.git ~/rplidar_sdk && make -C ~/rplidar_sdk
    python3 -m venv --system-site-packages ~/rbenv
    source ~/rbenv/bin/activate
    pip install -U elevenlabs          # only for --voice

Set `ELEVENLABS_API_KEY` in `~/.bashrc` for voice alerts. Never commit API keys.

## Run

    python3 tools/sensors/rescue_sensors.py --duration 30
    python3 tools/sensors/rescue_sensors.py --duration 60 --voice
    python3 tools/sensors/rescue_sensors.py --no-lidar --no-webcam   # AI Camera only

`detections.jsonl` in each run folder uses the `detection_frame` record format, so
the dashboard can replay it:

    .venv/bin/rescuebot-dashboard --camera-backend replay --replay-path ~/rescuebot_runs/<run>/detections.jsonl

Run folders contain video and photos. Do not commit them.

## Known limitations

- Only one program can use the AI Camera at a time. Stop the dashboard's live camera
  (`--camera-backend live`) before running `rescue_sensors.py`, and the reverse.

- Lidar distances are wrong until `--lidar-offset` is calibrated with `find_offset.py`.
- Snapshots come from the webcam, not the AI Camera.
- Tested with fake sensors and on the Pi without calibration; no physical acceptance.
