# Gemini triage handoff

branch: gemini-implementation (based on test/full-product 3faf0e1), worktree ~/rescuebot-gemini
Status: implemented, unit-tested, and run live on the Pi with the real Gemini API,
ElevenLabs, and the AI Camera (2026-09-27). Uncommitted.

## What it does

With `rescuebot-dashboard --gemini` (or `RESCUEBOT_GEMINI=1`), when the AI Camera finds a
person, app/rescuebot/gemini.py grabs the camera's annotated frame
(`http://127.0.0.1:<video-port>/snapshot.jpg`) and sends it to Gemini with the detector's
person boxes, numbered left to right (bearing, image span, confidence). Gemini answers in a
fixed JSON schema: unique_people, people_note, needs_help, urgency (none/low/medium/high),
posture, hazards, summary, recommended_action, spoken_alert.

- Unique people: Gemini counts each real person once; duplicate boxes on one person,
  reflections, posters, screens, and mannequins don't count. people_note says why the
  count differs from the boxes (live example: "boxes 1 and 2 are the same person").
- Triggers: a new sighting (no one for 3 s before), more person boxes than already
  assessed in this sighting, every 30 s while someone stays in view, or "Ask Gemini now".
  Calls are at least 8 s apart, one at a time, 20 s timeout.
- Voice: with `--voice`, Gemini is the robot's voice. Its spoken_alert (first person,
  at most 25 words: count, where, condition, help needed) is spoken via ElevenLabs for
  every new sighting and operator request, and on rechecks/extra boxes only when the
  unique count, urgency, or needs_help changed. The fixed "Person detected ..." alerts
  (CameraVoice) are muted while Gemini is active; if a Gemini call fails on a new
  sighting, the fixed alert is spoken instead. Without a key the fixed alerts stay on.
- Dashboard: camera lower-third shows "N unique people on screen" (plus "detector: M
  boxes" when they differ) while Gemini's answer is under 45 s old; fewer boxes than
  assessed caps the count, more shows "Gemini checking...". "Gemini triage" panel in the
  desk: urgency badge, the assessed frame, summary, action, unique people + note, posture,
  hazards, time/latency/model, earlier results, "Ask Gemini now".
- Saved: every assessment as `~/rescuebot_runs/gemini/<time>.jpg` + `.json`.

## Models

Default `gemini-3.5-flash-lite,gemini-3.1-flash-lite` (tried in order on 429/5xx/network
errors; a bad key or request fails at once). Measured 2026-09-27: 3.5-flash-lite 1.2-2.2 s
(one 4.8 s), 3.5-flash about 15 s even with thinking "minimal" (too slow to talk),
3.8-flash and flash-latest returned 503 (high demand), 2.5-flash is retired for new keys.
Change with `--gemini-model a,b` or `RESCUEBOT_GEMINI_MODEL`.

## Safety / isolation

Own thread; reads only camera status and the camera's HTTP snapshot. Never touches
control, arming, motors, or accessories. The `gemini_assess` WebSocket message is
read-only: it needs no control ownership and never calls stop (other unknown messages
still stop the robot).

## Setup

- Key: `GEMINI_API_KEY` (GOOGLE_API_KEY also accepted), sent only as the
  `x-goog-api-key` header. On this Pi it's an `export` line in ~/.bashrc, which is
  below the non-interactive `return` guard: a dashboard started from a script or nohup
  must load it first, e.g.
  `eval "$(grep -E '^export (GEMINI_API_KEY|ELEVENLABS_API_KEY)=' ~/.bashrc)"`.
- No new Python dependency (standard-library HTTP to the Gemini REST API).
- Needs `--camera-backend live` with video on (default port 8081). Without a key or live
  video the panel says why and nothing is called.

Standalone: `python -m rescuebot.gemini` (current live frame) or
`python -m rescuebot.gemini photo.jpg`.

## Validation

- `PYTHONPATH=app ~/rescuebot-full-product/.venv/bin/python -m pytest -q tests`:
  261 passed, 2 skipped (233 before + 28 Gemini/mute tests). Use PYTHONPATH=app from this
  worktree: the shared venv's editable install points at ~/rescuebot-full-product.
- Live on the Pi (bridge serial + dashboard `--motor-backend bridge --camera-backend live
  --sensors live --voice --auto-accessories --gemini`, run from this worktree): real
  person in view -> Gemini assessment in 1.6-2.2 s, spoken through ElevenLabs on the
  speaker; 2 detector boxes on one person -> "1 unique" with the note above; an unchanged
  recheck stayed quiet; urgency change (low -> medium) was spoken; "Ask Gemini now" over
  the WebSocket was accepted, answered, and spoken; robot stayed disarmed, motor healthy,
  throttled=0x0. Dashboard checked in headless Chromium (lower-third + panel); display
  rule checked for 9 count cases in Chromium.
- Not tested live: model fallback (unit-tested only), a real multi-person scene with two
  different people in separate boxes (Gemini did report "2 unique" once from one box).
