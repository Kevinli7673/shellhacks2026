# Rescuebot implementation plan

Status: approved for implementation.  
Milestone: manual driving + person detection + browser dashboard.

## 1. Authority and scope

This document incorporates the approved project architecture and subsequent
user clarifications. Explicit current user instructions take precedence.

The original rescue_robot_plan.md is preserved under docs/reference/.
Its long-term roadmap is context, not authorization to implement later features.
Where it differs, this implementation plan governs the current milestone.

Included:

- Browser dashboard and manual mecanum driving.
- Mock and serial motor backends.
- Command arbitration, ownership, safety, and watchdogs.
- ESP32-S2 firmware and BNO055 telemetry.
- Independent AI Camera person detection and annotated video.
- JSONL recording, detection replay, and tests.
- Working Git checkpoints and team handoffs.

Deferred until this milestone passes physical acceptance:

- Optional ROS 2 integration.
- LiDAR, SLAM, autonomous navigation, survivor localization, hazard mapping,
  A*, ESP32-C6 features, environmental sensors, Logitech integration,
  and unrelated polish.

User-authorized exception (2026-09-26): dashboard buttons for the robot's
buzzer and light (sections 3 and 6). They never affect driving.

Passing acceptance does not automatically authorize the deferred roadmap.

## 2. Architecture

Driving path:

    Laptop browser
      -> FastAPI/WebSocket
      -> command arbiter
      -> independent Python motor-bridge process
      -> USB serial
      -> ESP32-S2
      -> mecanum mixing
      -> I2C Adafruit motor shield
      -> four motors

Perception path:

    AI Camera / IMX500
      -> independent Picamera2 camera service
      -> generic detection events and annotated video
      -> browser dashboard

The ESP32-S2 is the sole motor controller and BNO055 reader.
The Pi must never directly control the motor shield.

Run Python services natively on Raspberry Pi OS.
ROS 2 and Docker are not prerequisites for driving or perception.

Keep camera capture, inference, video encoding, and streaming outside both
the control-service process and the motor-bridge process.

Use simple nonblocking Unix datagram IPC for arbiter-to-bridge commands.
Keep only the latest fresh command; include session identity and a monotonic
expiry time. Never play a backlog of movement commands.

If Unix socket setup becomes a development bottleneck, use bounded loopback
UDP with the same session, freshness, expiry, and nonblocking semantics.
Record the reason and tests in changes.md. Do not introduce a message broker.

## 3. Dashboard and controls

Use plain HTML/CSS/JavaScript served by FastAPI.

Display:

- Annotated video and person detections.
- Enable Driving and prominent Stop controls.
- Speed setting and control ownership.
- Motor connection, acknowledgment age, and fault reason.
- Camera online/stale/offline state.
- Explicit mock/live/replay labels.
- Requested wheel outputs for debugging.

| Input | Meaning |
|---|---|
| W / S | Forward / backward |
| A / D | Strafe left / right |
| Left / Right Arrow | Counterclockwise / clockwise rotation |
| Up / Down Arrow | Increase / decrease speed setting |
| Space or Stop button | Stop and disable driving |
| Release movement keys | Zero movement |

Command coordinate convention:

- Positive forward = W = physical forward.
- Positive sideways = D = physical right strafe.
- Positive turn = Right Arrow = physical clockwise rotation.

Opposing inputs cancel. Combined translation and rotation are supported.

Start at 30% of the configured motor-output ceiling.
Adjust by 10 percentage points per press, within 10-100%.
Ignore keyboard auto-repeat for speed adjustments.
Speed keys alone cannot cause movement.
Prevent control keys from scrolling the page.

These settings represent motor commands, not measured ground speed.

Buzzer and Light toggle buttons switch the active buzzer and the NeoPixel
Jewel (all RGBW channels at 255: maximum-brightness white). Only the browser
that owns driving can toggle them, armed or disarmed; they show the state the
firmware confirmed. Stop does not change them. They switch off at firmware
boot, on a new serial session, and when the owning browser disconnects.

## 4. Mecanum mixing

The ESP32 performs mixing. Mock mode reproduces the same calculation.

1. Normalize the forward/sideways vector if its magnitude exceeds one.
2. Scale forward, sideways, and turn by the effective PWM limit.
3. Apply exactly:

       front_left  = forward + sideways + turn
       front_right = forward - sideways - turn
       rear_left   = forward - sideways + turn
       rear_right  = forward + sideways - turn

4. If any absolute output exceeds the limit, scale all four together.
5. Round consistently, then apply configured wheel mapping and inversions.

Handle a zero limit explicitly by producing four zero outputs.

Keep the pure calculation separate from hardware access.
Use shared fixtures to verify Python/C++ output parity, including all
keyboard combinations and normalization boundaries.

Explicit logical-direction tests before mapping/inversions:

- Positive forward: all four outputs positive.
- Positive sideways: FL/RR positive, FR/RL negative.
- Positive turn: FL/RL positive, FR/RR negative.
- Negative commands reverse the corresponding outputs.

These tests do not establish physical chassis direction.
First verify wheel mapping and rotation with the chassis raised.
Then verify physical forward, right strafe, and clockwise rotation on the
floor at low power. Correct wiring differences through mapping/inversions,
not changes to the core equations.

## 5. Arbitration and ownership

Exactly one arbiter supplies commands to the motor bridge.

Priority:

1. Latched Stop or fault.
2. Manual control.
3. Future autonomous input.

Autonomous input is disabled in this milestone.

Manual ownership suppresses autonomous input even when movement is zero.
Releasing a key must not hand control to autonomy.

Only one browser session can own driving.
Other sessions are read-only but may request Stop.

Arming requires:

- Explicit Enable Driving.
- Released movement keys.
- Healthy control and motor connections.
- Successful firmware arm acknowledgment.

Reconnection or a transport handshake must never arm the robot.

## 6. Serial protocol

Use newline-delimited JSON at 115200 baud.

Example drive packet:

    {
      "type": "drive",
      "session": "...",
      "seq": 142,
      "forward": 1.0,
      "sideways": 0.0,
      "turn": 0.0,
      "speed_limit": 60
    }

Axes are finite numbers in [-1, 1].
speed_limit is an integer PWM ceiling in [0, 255], additionally bounded
by the firmware's configured hardware ceiling.

Acknowledgments include the accepted sequence, session, and computed outputs:

    {
      "session": "...",
      "ack": 142,
      "fl": 60,
      "fr": 60,
      "rl": 60,
      "rr": 60
    }

Sequence numbers increase within a negotiated session.
Fresh sessions after reboot/reconnection invalidate old commands.

Include explicit arm/disarm messages.
Use bounded packet parsing and nonblocking serial handling.

Malformed, oversized, non-finite, out-of-range, duplicate, and out-of-order
packets must not drive motors or refresh the motion watchdog.
Malformed movement packets stop and disarm.
Stale packets cannot restore movement.

Do not retransmit old movement packets.
Send freshly computed current commands instead.

The firmware emits separate timestamped BNO055 heading/calibration/status
telemetry at approximately 20 Hz. IMU messages cannot refresh the motor
watchdog or count as motor-command acknowledgments.

Buzzer and light use their own message, accepted armed or disarmed:

    {"type": "accessories", "session": "...", "seq": 7, "buzzer": true, "light": false}

The firmware answers with `accessories_ack` echoing session, seq, buzzer, and
light. A repeated or older seq in the same session is ignored. Accessories
messages never refresh the motion watchdog, arm, disarm, or count as motor
acknowledgments; a malformed one is still a malformed packet.

## 7. Safety and firmware

| Layer | Behavior |
|---|---|
| Browser | Clear keys and disable on blur, hidden tab, disconnect, or Stop |
| Control service | Disable after 250 ms without fresh browser commands |
| Motor bridge | Disable after 250 ms without fresh arbiter commands or advancing ACKs |
| ESP32-S2 | Disable outputs after 500 ms without a valid fresh drive command |

The ESP32 watchdog is configurable and starts at 500 ms.
Do not reduce it to 300 ms without test evidence and a recorded decision.

Send fresh commands at 20 Hz.
Stop events bypass ordinary movement cadence and any acceleration behavior.

Boot, faults, watchdog expiry, and reconnects leave driving disabled.
Discard previously held keys and invalidate the old driving session.
Require explicit Enable Driving before movement can resume.

Initialize motor outputs off before sensor initialization.
Use bounded I2C operations and avoid blocking sensor retry loops.
Missing IMU data reports unavailable and does not block manual driving.

Motor stop means output is disabled. RELEASE does not actively brake wheels.
Measure physical stopping behavior separately.
A locked driver bus or motor-driver fault is not something a software
watchdog alone can guarantee recovery from.

Hardware configuration must specify:

- Exact ESP32-S2 board and I2C pins.
- Motor shield revision/address.
- Wheel channel mapping and direction inversions.
- Validated motor-output ceiling.

Do not assume the original document's approximate 80% PWM cap is universally
safe. Keep real driving disabled until hardware configuration is validated.

## 8. Camera and model-independent detections

Support:

- motor_backend = mock | serial
- camera_backend = mock | live | replay

The live camera provider uses Picamera2 and an IMX500 model.
Model path, label mapping, and confidence threshold are configurable.
Downstream code must not depend on YOLOv8n-specific outputs.

Each frame event contains:

- Capture timestamp.
- Frame ID and camera ID.
- Image dimensions.
- A detections list.

Each detection contains:

    {
      "label": "person",
      "confidence": 0.87,
      "bbox": {
        "x": 0.20,
        "y": 0.15,
        "width": 0.30,
        "height": 0.60
      }
    }

Bounding boxes use normalized displayed-image coordinates:
origin at top-left, x rightward, y downward.

Convert model-specific class IDs, cropping, scaling, and coordinates inside
the provider. Keep event timestamps tied to capture time.

Default confidence threshold: 0.5.
Publish empty detection lists when no people are detected.
Expire results after one second without fresh events.

Distinguish valid empty results from stale/offline inference.

Annotate frames in the camera service using corresponding frame metadata.
Start with a 640x480 preview and up to 10 streamed frames per second.
Keep only the newest frame for slow consumers.
Do not accumulate video or detection backlogs.

Serve video directly from the independent camera service with browser access
configured for the dashboard origin. Camera failure must not stop control
processing or delay a Stop command.

## 9. Recording and replay

Use JSONL to record:

- Frame detection events.
- Selected movement commands.
- Motor acknowledgments and computed outputs.
- Safety-state transitions.

Logging uses bounded asynchronous queues and cannot block control.
Report dropped log entries.

Replay detections at recorded intervals.
Rebase replay delivery timestamps while preserving original timestamps.
Provide fixtures for appearance, disappearance, overlaps, empty frames,
and stale/offline behavior.

Motor-command recordings are for inspection and mock playback only.
Never replay recorded movement through the serial backend.

## 10. Build sequence

Stage 0:

- Create this file, AGENTS.md, and changes.md.
- Preserve the original plan under docs/reference/.
- Add README links.
- Commit the documentation baseline.

Then follow this order:

| Stage | Working checkpoint |
|---|---|
| A | Dashboard UI and mock status/video |
| B | Mock motor backend and visible wheel outputs |
| C | Keyboard handling, speed adjustment, and mixing |
| D | All ownership, stop, timeout, and rearming behavior |
| E | Serial protocol and simulated communication failures |
| F | ESP32-S2 firmware, shield adapter, watchdog, and IMU telemetry |
| G | Real backend and explicitly enabled low-power driving |
| H | Independent AI Camera capture service |
| I | Generic person-detection events |
| J | Correct annotations and bounded-latency video |
| K | Dashboard integration, recording, replay, and acceptance testing |
| L | Optional ROS 2 adapters, only after physical milestone acceptance |

Do not parallelize dependent stages prematurely.
Independent work may overlap after shared interfaces are agreed.

ROS adapters must preserve browser controls and the ESP32 protocol.
Any future movement source enters through the arbiter.
Convert physical velocity units and ROS axes explicitly; do not present
normalized PWM commands as measured velocity.

## 11. Tests and definition of done

Automated tests:

- All keys, combinations, opposing inputs, and speed boundaries.
- Explicit forward/right/clockwise coordinate-convention tests.
- Diagonal normalization and common wheel-output normalization.
- Python/C++ mixing parity.
- Stop priority, ownership, expiry, session invalidation, and rearming.
- Fragmented/combined serial packets, malformed values, missing ACKs,
  stale sequences, reconnects, and watchdog expiry.
- Detection coordinate conversion, empty results, expiration, and replay.
- Camera failure and slow consumers while control remains responsive.

Physical acceptance:

- W/S move forward/backward.
- A/D strafe left/right.
- Arrows rotate counterclockwise/clockwise.
- Combined movement and speed adjustment work correctly.
- Speed adjustment alone never moves the robot.
- Releasing movement keys stops movement.
- Space immediately disables driving.
- Blur, tab switching, WebSocket/network loss, server failure,
  bridge failure, and USB loss result in motor-output shutdown.
- Malformed serial packets cannot drive motors.
- Reconnection never resumes movement.
- Real driving requires explicit Enable Driving.
- Only one browser controls movement.
- AI Camera person detection works under venue lighting.
- Bounding boxes align; stale detections disappear.
- Camera failure is visible and cannot interfere with motor control.
- Video latency does not accumulate.
- Complete a 15-minute continuous driving/detection/video run.

Record measured output-cutoff timing and physical stopping behavior.
Mock results and successful firmware compilation do not establish
physical acceptance.

## 12. Collaboration and handoff

Follow AGENTS.md.
Keep changes.md current with task ownership, progress, decisions, tests,
blockers, and exact next steps.

Use separate branches/worktrees for concurrent workstreams.
Follow WORKSTREAMS.md for active branch boundaries and merge gates.
Commit passing checkpoints and preserve immutable checkpoint references.
Never label untested code as a known-good robot version.

Keep current architecture in this file, operating commands in README,
and historical evidence in changes.md. Avoid conflicting duplicated specs.
