# Speaker handoff

Branch: test/speaker (based on test/full-stack d701c3b)
Status: speaker hardware and ElevenLabs verified on the Pi; dashboard `--voice` implemented and unit-tested, not yet run live

There is no touch display and no USB webcam on the robot anymore. Audio goes
through a MAX98357A I2S amp to a 1 W speaker.

## Hardware

| Amp pin | Pi pin |
|---|---|
| Vin | 2 (5 V) |
| GND | 6 |
| BCLK | 12 (GPIO 18) |
| LRC | 35 (GPIO 19) |
| DIN | 40 (GPIO 21) |

GAIN and SD are unconnected. `dtoverlay=max98357a` is in
/boot/firmware/config.txt. The amp is ALSA card 2 and the default PipeWire sink.
Keep the sink volume around 0.6 (`wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.6`),
because the amp can overdrive the speaker.

## Tools

- tools/speaker/speaker_setup.sh: one-time setup (espeak-ng, alsa-utils, overlay).
- tools/speaker/speaker_test.py: plays beeps, the victim-found alert, and an
  espeak-ng voice line straight to the MAX98357A card. `--say "text"` speaks text.
- `python -m rescuebot.voice "text"` (or tools/sensors/voice_alerts.py, now a
  shim): ElevenLabs (eleven_flash_v2_5) speech, cached as MP3 in
  ~/.cache/rescuebot_voice/. It needs `pip install -e '.[voice]'` and
  `ELEVENLABS_API_KEY` in ~/.bashrc. Never commit the key.

## Verified 2026-09-26 on the Pi

- speaker_test.py: card found, all three steps played, exit 0.
- voice_alerts.py produced new MP3s from the ElevenLabs API (not the cache) and
  played them. espeak-ng fallback runs.
- The full test suite on this branch: 141 passed, 2 skipped.

## Dashboard voice (`rescuebot-dashboard --voice`)

- app/rescuebot/voice.py (moved from tools/sensors/voice_alerts.py with history):
  `Speaker` takes injectable synthesizer/player/fallback/clock; any error in
  synthesis or playback is logged and the thread keeps running. ElevenLabs
  failure -> espeak-ng -> silent.
- `CameraVoice` runs on its own daemon thread: every 0.5 s it reads the camera
  status, and when the camera is `online` it adds `bearing_deg` to each
  detection from the bbox center (`x_to_bearing`, 66 degree FOV; ahead within
  10 degrees) and calls `Speaker.say(alert_text(...))`, which never blocks.
- web.py: `--voice` (or RESCUEBOT_VOICE=1), default off. It never touches the
  control loop or Stop. Camera status reads from the web handlers and the voice
  thread share one lock (`_LockedCamera`), because ReplayCameraBackend.status()
  mutates state. On start it says "Rescue bot online. Scanning for survivors."
  and pre-caches the ahead/left/right person phrases.
- pyproject.toml: optional extra `voice = ["elevenlabs>=2,<3"]`.
- tests/sensors/test_voice.py (fake synthesizer): cooldown, queue drops extra
  alerts, left/right/ahead from box positions, network/player failures never
  raise, offline camera is silent, dashboard starts/closes voice and Stop works.

Validation 2026-09-26 on the Pi (Python 3.13, .venv):
`.venv/bin/python -m pytest -q` -> 158 passed, 2 skipped, 1 warning (httpx
deprecation), 192 subtests passed. Unit tests only; the dashboard has not yet
been run with `--voice` on the robot.

## Open

- Live check: `rescuebot-dashboard --motor-backend bridge --camera-backend live
  --sensors live --voice`, then stand ahead, left, and right of the camera.
- Optional: survivor line with eleven_v3, operator text-to-speech box.
- Before the demo: trigger every phrase once on good Wi-Fi so it is cached.
