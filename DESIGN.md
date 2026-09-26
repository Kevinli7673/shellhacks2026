---
name: Rescuebot
description: Operator gallery for a mecanum search robot, built like a broadcast control room.
colors:
  tally-red: "oklch(0.63 0.215 27)"
  tally-red-deep: "oklch(0.46 0.17 27)"
  tally-amber: "oklch(0.82 0.155 78)"
  lamp-green: "oklch(0.76 0.16 152)"
  stop: "oklch(0.585 0.215 27)"
  stop-hover: "oklch(0.64 0.22 27)"
  stop-press: "oklch(0.52 0.2 27)"
  gallery-desk: "oklch(0.215 0.009 68)"
  desk-raised: "oklch(0.255 0.01 68)"
  desk-line: "oklch(0.36 0.01 68)"
  bezel: "oklch(0.135 0.005 68)"
  screen: "oklch(0.175 0.012 245)"
  umd-black: "oklch(0.1 0.003 68)"
  lamp-off: "oklch(0.34 0.008 68)"
  ink: "oklch(0.955 0.006 80)"
  ink-secondary: "oklch(0.8 0.012 76)"
  ink-tertiary: "oklch(0.7 0.014 74)"
typography:
  display:
    fontFamily: "B612, ui-sans-serif, system-ui, sans-serif"
    fontSize: "2.488rem"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "0.01em"
  headline:
    fontFamily: "B612, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.728rem"
    fontWeight: 700
  title:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "1.2rem"
    fontWeight: 800
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.4
    fontFeature: "\"tnum\" 1"
  label:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 700
    letterSpacing: "0.1em"
  data:
    fontFamily: "ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "0.75rem"
    fontWeight: 500
rounded:
  hairline: "2px"
  panel: "4px"
  keycap: "5px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "20px"
  xl: "24px"
components:
  button-stop:
    backgroundColor: "{colors.stop}"
    textColor: "{colors.ink}"
    typography: "{typography.headline}"
    rounded: "{rounded.panel}"
    height: "72px"
  button-stop-hover:
    backgroundColor: "{colors.stop-hover}"
  button-stop-active:
    backgroundColor: "{colors.stop-press}"
  button-enable:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.panel}"
    height: "56px"
  umd-tag:
    backgroundColor: "{colors.umd-black}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.hairline}"
    padding: "6px 10px"
  lower-third:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.umd-black}"
    typography: "{typography.title}"
    rounded: "{rounded.hairline}"
    padding: "6px 13px"
  keycap:
    backgroundColor: "oklch(0.29 0.009 68)"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.keycap}"
    size: "40px"
  keycap-lit:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.umd-black}"
  keycap-lit-armed:
    backgroundColor: "{colors.tally-red}"
    textColor: "{colors.ink}"
---

# Design System: Rescuebot

## Overview

**Creative North Star: "The Tally Gallery"**

Rescuebot's dashboard is a broadcast control room with one camera. The robot's view is the program monitor, and drive state is its tally light: the monitor frame and the gallery bar go from dark, to pulsing amber while arming, to solid red when the robot is armed. An operator at arm's length and a judge across the room read the same state at once.

The desk is warm graphite. Monitors are near-black glass in darker bezels, and every label on screen follows the under-monitor-display (UMD) grammar: uppercase white on black, tightly tracked. Saturation belongs to state alone. Red means the robot is hot or Stop, amber means arming or replay, and green is a small healthy-link lamp. Nothing is decorative, and nothing imitates a physical material the page does not render.

It is an Operate surface: dense, consistent, and familiar where controls are concerned. Its character lives in the tally, the UMD tags, the lower-third, and the B612 readout lettering. Each fact appears once on the page.

**Key Characteristics:**
- Drive state is shown three ways at once: tally color, a large B612 word, and a reason line.
- Warm graphite neutrals; saturated color only for state.
- Flat materials: solid fills, 1px keylines, offset drop shadows only.
- Broadcast grammar: program monitor, UMD tags, lower-third.

## Colors

A restrained palette of warm graphite and cool monitor black, where every saturated hue is a state.

### Primary
- **Tally Red** (tally-red): The armed state. It colors the monitor's tally frame, the gallery bar rule, the DRIVING tag, the drive-state lamp, and lit keycaps while armed. It always means the robot can move.
- **Stop Red** (stop, stop-hover, stop-press): Only the Stop button. It is slightly deeper than tally red, so the control and the state never read as one element.

### Secondary
- **Tally Amber** (tally-amber): Arming (a pulsing frame and blinking lamp), a stale camera, the replay source tag, focus rings, and text selection.

### Tertiary
- **Lamp Green** (lamp-green): Small round lamps for a healthy link (browser, motor bridge, firmware armed, camera online). It is never used on larger surfaces.

### Neutral
- **Gallery Desk** (gallery-desk): The page ground.
- **Raised Desk** (desk-raised): The drive-desk panel.
- **Desk Line** (desk-line): Panel borders, dividers, keycap keylines, and the idle tally.
- **Bezel** (bezel): The monitor surround.
- **Monitor Screen** (screen): The camera field. It is the one cool neutral and marks the video area.
- **UMD Black** (umd-black): The gallery bar, UMD tags and strips, and the signal-chain strip.
- **Lamp Off** (lamp-off): Unlit lamps and the idle tally.
- **Monitor White** (ink), **Label Warm** (ink-secondary), **Caption Warm** (ink-tertiary): Text in three steps, tinted warm and never neutral gray.

### Named Rules
**The Tally Rule.** Saturated red and amber are reserved for drive state and Stop. A decorative element never uses them.

**The Two Reds Rule.** Stop (stop) and the armed state (tally-red) are kept as separate tokens. Never merge them.

## Typography

**Display Font:** B612 Bold, the Airbus cockpit display face, self-hosted from `app/rescuebot/static/fonts/` (SIL OFL 1.1), falling back to the system sans.
**Body Font:** The platform system sans stack.
**Label/Mono Font:** The platform monospace, used only for PWM, ages, and other measurements.

**Character:** Cockpit lettering for the few words that must be read under stress, and a quiet workhorse sans for everything else.

### Hierarchy
- **Display** (B612 700, 2.488rem, 1.05): The drive-state readout ("ARMED · DRIVING") and the NO SIGNAL slate.
- **Headline** (B612 700, 1.728rem): The Stop label and speed value.
- **Title** (system 800, 1.2rem): The wordmark (B612) and the lower-third detection count.
- **Body** (system 400, 1rem, 1.4, tabular figures): Reason lines, key-map descriptions, and slate messages. Max 52ch on the reason line.
- **Label** (system 700, 0.875rem, 0.1em, uppercase): UMD tags, the DRIVING tag, desk labels, and signal-chain names (0.75rem).
- **Data** (mono 500, 0.75–1rem): PWM and telemetry values.

### Named Rules
**The Stress Words Rule.** B612 is only for words an operator must read in under a second: drive state, Stop, the speed value, the slate, and the wordmark.

**The Measurement Mono Rule.** Monospace appears only on numbers that are measurements or time, never as a "technical" costume.

## Layout

The layout is a two-column gallery with a maximum width of 1600px and 20px gutters. The program monitor (left) and drive desk (right) split the width evenly, and the signal-chain and telemetry strip spans full width beneath them. Below 1500px the chain and telemetry stack inside that strip. Below 900px everything becomes one column, and the Enable and Stop row becomes a fixed bottom bar so Stop is always reachable. Below 520px the station name hides and the readouts step down a size. Spacing follows a 4/8/16/20/24px rhythm, and the desk uses 24px gaps between its groups.

## Elevation & Depth

The world is flat. Depth comes from tonal layering (desk, raised desk, bezel, screen) and 1px keylines. Only three drop shadows exist, each with a real offset and blur:

### Shadow Vocabulary
- **Monitor lift** (`0 14px 30px -12px oklch(0 0 0 / 0.7)`): The program monitor bezel.
- **Stop lift** (`0 10px 22px -12px oklch(0.4 0.18 27 / 0.9)`): The Stop button. It is removed while pressed.
- **Graphic lift** (`0 6px 14px -6px oklch(0 0 0 / 0.6)`): The lower-third.

### Named Rules
**The No Imitation Rule.** No bevels, specular highlights, gradients faking plastic or metal, or scanline textures. Lamps are flat discs with a dark 1px ring, and keycaps are flat fills with a keyline.

## Shapes

Corners are small and square-shouldered: 2px on UMD tags, lower-thirds, and screens; 4px on panels and buttons; 5px on keycaps. Round shapes are lamps and the tally. The tally is a 6px outline ring around the monitor bezel, not a border inside it, so the frame reads from a distance.

## Components

### Buttons
- **Stop:** The largest control on the page (72px tall, 56px or more on mobile), flat Stop Red with white B612 text and a Space keycap badge. It lifts on hover, drops its shadow when pressed, and has an amber 3px focus ring.
- **Enable driving:** A 2px outlined ghost button in Label Warm. It relabels itself as "Arming…", "Driving enabled", or "Read-only", and it is disabled whenever it cannot act.

### Program monitor (signature)
A 4:3 screen in a 12px bezel, with the tally ring outside. It carries a source UMD tag top-left (`Cam 1 · Mock/Replay/Live`, amber when the source is replay), a camera lamp tag top-right, and a white lower-third bottom-left. There is nothing beneath the monitor; frame age lives in the telemetry. Detections are 2px white boxes with a black keyline and white label chips, drawn above the tags. Offline and stale states show a centered B612 slate.

### Drive readout
A 24px lamp and a B612 state word, colored by state, above a reason line. Faults append their machine code in small mono.

### Speed readout
A single line: a "Motor speed" label, the percentage in B612, and the mono PWM value beside it. It is adjusted only with Up/Down. There is no slider.

### Keycap panel
WASD and arrow clusters as 40px flat keycaps, with short legends (Move · strafe, Rotate · speed) instead of a separate key list. Held keys light (ink, or tally red for movement keys while armed), so the audience sees the operator's input.

### Signal chain
A UMD-black strip of four links (Browser, Control, Motor bridge or backend, Firmware), each with a lamp, an uppercase name, and a value, joined by thin arrows. It is the only place connection and ownership appear. Below it, telemetry shows a 2×2 wheel PWM grid and a readings list three to a row (two on phones).

## Do's and Don'ts

### Do:
- **Do** show drive state with color, a word, and a reason together; never color alone.
- **Do** keep every source labeled (Mock, Replay, Live) on the monitor.
- **Do** theme browser surfaces: amber focus rings (3px, 3px offset), amber selection, and graphite scrollbars.
- **Do** respect `prefers-reduced-motion`: the arming pulse and lamp blink become static amber.
- **Do** keep transitions between 120 and 200ms (`cubic-bezier(0.16, 1, 0.3, 1)`), and only for state changes.

### Don't:
- **Don't** use tally red or amber for anything that is not drive state, arming, or replay.
- **Don't** add eyebrow labels above headings, card grids, gradient text, or glyph icons; icons are authored inline SVG with a 2px round stroke.
- **Don't** imitate materials with bevels, gloss, or scanlines.
- **Don't** cover the camera with anything other than the source and camera tags, detections, and the lower-third.
- **Don't** repeat a fact in two places (connection, ownership, source, frame age); each has one home.
