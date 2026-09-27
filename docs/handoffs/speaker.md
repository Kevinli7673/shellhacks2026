# Speaker handoff

Branch: test/speaker (based on test/full-stack 9119cf3)
Status: speaker hardware and ElevenLabs verified on the Pi; not yet wired into the dashboard

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
- tools/sensors/voice_alerts.py "text": ElevenLabs (eleven_flash_v2_5) speech,
  cached as MP3 in ~/.cache/rescuebot_voice/. It needs `pip install elevenlabs`
  in the venv and `ELEVENLABS_API_KEY` in ~/.bashrc. Never commit the key.

## Verified 2026-09-26 on the Pi

- speaker_test.py: card found, all three steps played, exit 0.
- voice_alerts.py produced new MP3s from the ElevenLabs API (not the cache) and
  played them. espeak-ng fallback runs.
- The full test suite on this branch: 141 passed, 2 skipped.

## Open

- The dashboard does not speak yet. Next steps: move voice_alerts.py into
  app/rescuebot/voice.py, add `--voice` to web.py on its own thread (never
  touching the control loop or Stop), derive `bearing_deg` from the bbox center
  (66 degree FOV), and add tests with a fake synthesizer.
