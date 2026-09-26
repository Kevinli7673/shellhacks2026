# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Operator:** one teammate at a laptop driving the mecanum robot with the
  keyboard. Depending on the run, the robot is either in view or out of
  sight behind the "collapsed building" course, when the dashboard (camera,
  detections, and robot state) is their only view of it.
- **Judges and audience:** watch the same screen, shown or projected, during
  demos and judging. They must be able to follow what the robot sees and
  whether it is armed from a distance.
- **Teammates testing:** read state, faults, and wheel values while
  debugging.

## Product Purpose

Rescuebot is a mecanum-wheel search robot built for ShellHacks 2026. The
dashboard lets one operator drive it safely by keyboard and see people the
AI Camera detects. Success for this milestone: reliable manual driving with
explicit arming, an always-available Stop, person detection with aligned
boxes, and honest robot, motor, and camera state.

## Positioning

Person detection runs on the camera (IMX500) and reaches the browser as
model-agnostic events, while every motor command passes an explicit
safety chain: browser, arbiter, motor bridge, then an ESP32 watchdog. The
dashboard is where that chain is visible. It shows whether the robot is
armed, what it sees, and why it stopped.

## Operating Context

- Laptop browser on the venue network, keyboard driving: W/S forward and
  back, A/D strafe, arrows rotate, Up/Down speed, Space stops.
- Venue lighting, a cardboard-maze course, and hackathon demo conditions,
  with a crowd around the screen during judging.
- One browser owns driving; other browsers are read-only but may request
  Stop.

## Capabilities and Constraints

- Plain HTML/CSS/JavaScript served by FastAPI. No frontend framework or
  build step.
- Required on screen (IMPLEMENTATION_PLAN.md section 3): annotated video and
  person detections; Enable Driving and a prominent Stop; speed setting and
  control ownership; motor connection, acknowledgment age, and fault
  reason; camera online/stale/offline state; explicit mock/live/replay
  labels; requested wheel outputs for debugging.
- Safety behavior is fixed and must not change: Stop overrides everything;
  blur or hidden tab clears keys and stops; reconnects never re-arm; Enable
  requires released keys and, with the bridge, firmware confirmation
  (shown as "Arming…", 1 s timeout).
- Speed values are motor-command percentages, not measured ground speed.
- Live camera video is not integrated yet; mock and replay camera backends
  exist. LiDAR, maps, autonomy, and ROS 2 are out of scope for this
  milestone.

## Brand Commitments

- Name: "Rescuebot". No logo, palette, or slogan exists.

## Evidence on Hand

- Detection replay fixtures in fixtures/detections/ (synthetic person
  boxes). No real camera footage, photos, or logos are in the repository.
  Do not fabricate rescue statistics, partner organizations, or claims of
  real-world deployment.

## Product Principles

1. Safety state is never ambiguous: armed, arming, disabled, and the reason
   for any stop are always readable at a glance.
2. The robot's view comes first when the operator drives blind; nothing
   covers the camera and detections.
3. Readable from across a room without failing the operator at arm's length.
4. Honest about what is simulated: mock, replay, and live sources are
   always labeled.
5. Controls behave the same every time; no surprise interactions during a
   run.

## Accessibility & Inclusion

- Full keyboard operation is the primary input. Stop must also be a large
  pointer target.
- State must not depend on color alone, since it is viewed on projectors
  and in varied lighting.
