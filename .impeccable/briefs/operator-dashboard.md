# Surface brief: operator dashboard

Target: app/rescuebot/static/index.html, styles.css, dashboard.js
Mode: Operate
Build path: code-led (no image generation available)
Launcher: unavailable (guidance-only install); concept-seed not run, so
directions were ranked by hand and presented through the structured tool.

## Scope

The single dashboard page: camera with person detections, drive state and
controls, speed, ownership, signal chain, and debug telemetry. The safety
behavior in dashboard.js (keys, heartbeat, blur/hidden stop, reconnect) is
fixed and must not change.

## Confirmed answers

- The operator drives both in sight and blind; judges and an audience watch
  the same screen, sometimes projected.
- Camera and controls share the screen evenly.
- Debug telemetry is compact and always on.
- Identity: the name "Rescuebot" only.

## Direction contract

THESIS: A broadcast gallery for one live camera. The robot's view is the
program monitor and drive state is its tally light, so the whole room knows
whether the robot is hot. Refuses the neon robotics HUD with radar sweeps and
the card-grid admin dashboard.

OWN-WORLD: A warm graphite gallery desk with near-black monitor bezels.
Under-monitor display (UMD) strips carry uppercase white labels on black.
Tally amber means arming and tally red means armed; a small green lamp means
a healthy link. Stop is the one large red control. Detection graphics are
white broadcast boxes with black keylines. System sans throughout, with
tabular figures for values and mono only for timecode and measurements.

STORY: The operator and the room see what the robot sees, whether it is
live, who holds control, and why it stopped. Teammates read the chain from
browser to firmware and the wheel values without leaving the page.

FIRST VIEWPORT: A full-width gallery bar (wordmark, link lamp, ownership,
session timecode). Below it, the left half is the program monitor: a 4:3
screen inside a bezel whose border is the tally, a source UMD tag top-left
(CAM 1 · MOCK/REPLAY/LIVE), a camera lamp top-right, and a lower-third
detection count. The right half is the drive desk: the state readout with a
tally lamp, a reason line, Enable plus the largest button (Stop, with a
Space keycap), a 10-detent speed fader, and a keycap panel. A full-width
signal chain and telemetry strip closes the page.

FORM: Broadcast gallery, position 1 of 7 on the hand-ranked list. Seed key:
none (launcher unavailable). Raise from the mimic-panel alternate: the
signal-chain strip with a lamp and ack age per link, and wheel PWM placed at
the chassis corners.

SIGNATURE INTERACTION: The tally. The monitor frame and gallery bar go from
dark to pulsing amber (arming) to solid red (armed) at 150–200 ms, and the
keycaps light while keys are held so the audience sees the operator's input.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Revisions

- 2026-09-26 distill (user request): removed the speed fader (speed is now a
  one-line readout), the session timecode, the monitor UMD strip, the key-map
  list, and the gallery bar's connection/ownership readouts. Each fact now
  appears once. FIRST VIEWPORT is otherwise unchanged.

## Unresolved

- Live video is not integrated; the monitor shows a NO SIGNAL slate or replay
  boxes until the camera service lands.
