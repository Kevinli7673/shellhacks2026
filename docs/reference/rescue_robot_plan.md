# Search & Rescue Robot — Project Plan

A mecanum-wheel robot that explores an unknown "collapsed building" (a cardboard maze), builds a live 2D map with LiDAR, finds survivors with an AI camera, marks hazards, and plans the safest route back out for rescuers.

**Pitch line:** *"Our robot gives rescuers a map showing where survivors are, where the danger is, and the safest route in — before anyone has to enter the building."*

---

## 1. The demo (what judges see)

1. The robot drives into the cardboard maze.
2. A **live map** draws itself on a laptop/phone as it explores.
3. The camera spots a hidden "victim" (a teammate crouching in the maze works best).
4. A **red marker** appears on the map at the victim's position, with a **photo** of them. The face screen shows "FOUND!" and the buzzer/LED alert goes off.
5. **Hazard zones** (heat/fire from the flame and temperature sensors) show up on the map.
6. The robot plans the **shortest safe path back out**, draws it on the map, and drives it.

## 2. Why 2D mapping (and not 3D)

- What rescuers actually need is a **floor plan**: layout, victim locations, hazards, and a safe route. A 2D LiDAR map delivers all of that.
- A "3D view" of a 2D map (walls extruded to a fixed height, a.k.a. 2.5D) only looks nice — it adds no real rescue information, and it's wrong for tables, low objects, and overhangs.
- True 3D (rubble, ceiling collapse, crawl space) would need a tilting LiDAR on a servo. That's a possible future upgrade, not part of the core demo.
- **Be honest in the pitch:** "We map with a 2D LiDAR because a floor plan is what rescuers use."

---

## 3. Hardware

### Already have
| Part | Role |
|---|---|
| Raspberry Pi 5 (with cooler) | Main brain: mapping, detection, planning, dashboard |
| Raspberry Pi AI Camera (IMX500) | Person detection running **on the camera chip** |
| Logitech 1080p webcam | Live video stream for "rescuers" / backup camera |
| Slamtec RPLIDAR C1 (with CP210x USB adapter) | 360° distance scans for mapping |
| ESP32-S2 | Motor control + reads the IMU |
| ESP32-C6-LCD-1.47 | Robot "face" / status screen |
| 37-in-1 sensor kit | Flame, temperature, sound sensors, buzzer, RGB LED |
| Jumper wires, breadboards | Wiring and prototyping |

### Bought at Micro Center (Miami)
| Part | Price | Notes |
|---|---|---|
| Hiwonder Mecanum Wheel Chassis Car Kit (SKU 030742) | $36.99 | 4× TT motors (1:120, treat as 3–6 V), mecanum wheels, brackets |
| Adafruit Motor/Stepper/Servo Shield v2 (SKU 008672) | $19.95 | 4 DC motor channels over I2C (TB6612 chips). **Needs soldering.** |
| Adafruit BNO055 + BMP280 BFF (SKU 704452) | $29.99 | Drift-free heading. Use only 3V, GND, SDA, SCL. **Needs soldering.** |
| 2× Adafruit 3×AA battery holders (with switch) | — | Wired in series = 6 cells for the motors |
| Adafruit 5-wire lever block connector | $4.99 | Joins the two battery packs; extra slots for shared ground |
| AA batteries | — | Rechargeable NiMH preferred. Alkaline is OK for testing (cap motor speed in code). |
| Energizer 10,000 mAh power bank, 22.5 W, PD 3.0 | $19.99 | Powers the Pi 5 (USB-C to USB-C cable included) |
| Weller 30 W soldering iron kit (WLIRK3012A) | $24.99 | Fine conical tip + spare chisel tips |
| MG Chemicals Sn63/Pb37 no-clean solder, 0.032" | — | Easiest solder for beginners. Wash hands after use. |

### Still to confirm / optional
- Soldering stand (strongly recommended), multimeter, wire strippers
- M3 standoffs + screws, zip ties, double-sided mounting tape
- **Check at home:** female-to-female and male-to-female jumper wires, USB cable for the ESP32-S2, microSD card for the Pi

---

## 4. System architecture

```
RPLIDAR C1 ──USB──┐
AI Camera ──CSI───┤
                  ▼
           Raspberry Pi 5  ──Wi-Fi──► Dashboard (phone/laptop)
             │        │
            USB    serial / ESP-NOW
             ▼        ▼
         ESP32-S2   ESP32-C6 face screen
          │     │
         I2C   I2C
          ▼     ▼
  Motor shield  BNO055 IMU
          │
          ▼
   4 TT motors + mecanum wheels
```

**Division of labor**
- **Pi 5 (thinks):** SLAM mapping, AI camera detections, victim localization, route planning, web dashboard.
- **ESP32-S2 (acts):** Receives `forward, sideways, turn` commands from the Pi, converts them to 4 wheel speeds, reads the BNO055 heading and sends it back ~20×/sec. Runs a **safety watchdog**: no command for 500 ms → motors stop.
- **ESP32-C6 (shows):** Face/status reactions (exploring, searching, FOUND!, heading home).

**Mecanum wheel mixing (on the ESP32-S2)**
```
front_left  = forward + sideways + turn
front_right = forward - sideways - turn
rear_left   = forward - sideways + turn
rear_right  = forward + sideways - turn
```
Wheels must be mounted so the rollers form an **X when viewed from above**.

---

## 5. Power

| Supply | Powers | Notes |
|---|---|---|
| 6× AA (two 3×AA holders in series) | Motor shield → motors | ~7.2 V NiMH / ~9 V fresh alkaline. Cap PWM in code (~80%) to protect the 6 V TT motors. |
| Energizer power bank (USB-C PD) | Raspberry Pi 5 (and its USB devices) | Keeps motor spikes from ever rebooting the Pi. |
| Pi USB port | ESP32-S2, LiDAR, webcam | |

**Wiring the battery packs (batteries removed first!)**
1. Cut the white JST plug off both holders; split and strip ~1 cm of each wire.
2. Holder 1 **red** + holder 2 **black** → lever connector (series join).
3. Holder 1 **black** → motor shield power terminal **GND**; holder 2 **red** → motor shield power terminal **+**.
4. Check with a multimeter: ~7–9 V before connecting to the shield.

**Important**
- Motor shield: remove the **VIN jumper** so motor power stays separate from logic power.
- **Common ground:** Battery GND, motor shield GND, and ESP32-S2 GND must all connect.
- Never let bare red and black wires of the same pack touch.
- Pi 5 on a power bank limits USB current to 600 mA by default. Add `usb_max_current_enable=1` to `/boot/firmware/config.txt`.

**I2C bus (ESP32-S2):** Motor shield (0x60), BNO055 (0x28), BMP280 (0x77) share the same SDA/SCL wires — no address conflicts. Run the motor shield's logic at 3.3 V.

---

## 6. Software stack

**Raspberry Pi OS + Python** (not ROS 2):
- The AI Camera's detection software is built for Raspberry Pi OS.
- ROS 2 has a steep learning curve that would eat hackathon time.

| Job | Tool |
|---|---|
| LiDAR reading | `rplidar` Python library (460800 baud for the C1) |
| Mapping (SLAM) | BreezySLAM (scan matching, works with LiDAR alone) + BNO055 heading |
| Person detection | AI Camera via `picamera2` with IMX500 models |
| Route planning | A* search on the occupancy grid (~40 lines of Python) |
| Dashboard | Flask web server on the Pi |
| ESP32 firmware | Arduino (Adafruit_MotorShield + Adafruit_BNO055 libraries) |
| Version control | Git — always keep the last working version |

---

## 7. How each piece works

**Mapping (SLAM).** Each LiDAR scan is compared to the map so far to figure out how the robot moved (scan matching), then added to the map. The map is an **occupancy grid**: each cell is wall, open, or unknown. The BNO055 heading keeps it from twisting, since mecanum wheels slip.

**Exploring.** Start with a **right-hand wall follower** (simple and reliable). Upgrade to **frontier exploration** (drive toward the nearest known/unknown boundary) if time allows.

**Finding victims.** The AI Camera detects a person → the Pi turns the person's position in the image into an angle → reads the LiDAR distance at that angle → combines it with the robot's position on the map → places the victim marker precisely.

**Hazards.** The flame and temperature sensors are read continuously; readings above a threshold mark a **danger zone** on the map, which the route planner avoids.

**Listening for survivors.** The sound sensor detects yelling or knocking and steers the robot toward it — useful when the victim is out of camera view.

**Route home.** A* finds the shortest path from the victim back to the entrance, keeping a safe distance from walls and hazard zones.

**Dashboard.** Live map, robot position, explored area, victim markers (click → photo), hazard zones, and the planned rescue route.

---

## 8. Build order (every step is a working demo)

| Step | Goal | When |
|---|---|---|
| 1. Drive | Chassis built; ESP32-S2 drives mecanum wheels from keyboard commands | Before hackathon |
| 2. See | Live LiDAR scan plotted on screen | Before hackathon |
| 3. Map | Drive manually while BreezySLAM builds the map | Hackathon (first big milestone) |
| 4. Explore | Wall follower explores autonomously while mapping | Hackathon |
| 5. Detect | AI Camera finds the victim; marker + photo on the map | Hackathon |
| 6. Rescue path | A* route back out, drawn and driven | Hackathon |
| 7. Polish | Hazard zones, sound detection, C6 face, buzzer/LED alert, dashboard styling | Hackathon |

If time runs out after step 4 or 5, it's still a strong demo.

**Time estimate:** ~6–10 hours before the hackathon (assembly, soldering, wiring, steps 1–2); ~15–20 hours at the hackathon (steps 3–7, building the maze, practicing the pitch).

---

## 9. Pre-hackathon checklist

- [ ] Charge the power bank and AA batteries
- [ ] Assemble the mecanum chassis (X pattern, label motor wires FL/FR/RL/RR)
- [ ] Solder headers on the motor shield (use the breadboard trick to hold pins straight)
- [ ] Solder headers on the BNO055 BFF
- [ ] Wire and test the 6×AA pack (~7–9 V on the multimeter)
- [ ] Flash Raspberry Pi OS, enable SSH, add `usb_max_current_enable=1`
- [ ] Test each motor's direction; flip in code or swap wires if backwards
- [ ] ESP32-S2 driving from keyboard (step 1)
- [ ] LiDAR scan plotting on the Pi (step 2)
- [ ] Test AI Camera person detection in real lighting
- [ ] Set up a Git repo for the team

---

## 10. Risks and fixes

| Risk | Fix |
|---|---|
| Map smears or drifts | Drive slowly, use BNO055 heading, make walls taller than the LiDAR (~20 cm) |
| Pi reboots randomly | Pi on its own power bank (already planned); common ground only |
| Wheels hum but don't move | Raise minimum PWM in firmware |
| Robot won't strafe | Wheels not in X pattern — remount |
| Detection misses the victim | Test early in venue lighting; use a real person as the victim |
| LiDAR sees cables as obstacles | Keep everything below the LiDAR's scan plane |
| A change breaks the demo | Commit working versions to Git; roll back |

---

## 11. Future upgrades

- **Tilting LiDAR for true 3D:** MG996R metal-gear servo + bracket + separate 4×AA pack for the servo + 470–1000 µF capacitor. Robot stops and scans in place.
- Two-way audio ("Help is on the way") via a small USB speaker.
- Multiple robots splitting the building and merging maps.
