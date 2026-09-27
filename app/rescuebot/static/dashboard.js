const movementKeys = new Set(["KeyW", "KeyS", "KeyA", "KeyD", "ArrowLeft", "ArrowRight"]);
const displayKeys = new Set([...movementKeys, "ArrowUp", "ArrowDown"]);
const heldKeys = new Set();
const litKeys = new Set();
let socket;
let connected = false;
let canControl = false;
let currentState;

const element = (id) => document.getElementById(id);

const reasons = {
  operator_stop: "Stopped by the operator.",
  browser_timeout: "Stopped: keyboard input from the browser timed out.",
  browser_disconnected: "Stopped: the controlling browser disconnected.",
  release_keys_before_enable: "Release every movement key, then enable driving.",
  backend_unavailable: "Motor bridge unavailable. Start the bridge, then enable driving.",
  arm_timeout: "Stopped: the robot controller did not confirm arming within 1 second.",
  boot: "Stopped: the robot controller rebooted. Enable driving again.",
  link_lost: "Stopped: the USB link to the robot controller was lost.",
  ack_timeout: "Stopped: the robot controller stopped acknowledging commands.",
  watchdog_expired: "Stopped: the robot controller's watchdog expired.",
  arbiter_timeout: "Stopped: the motor bridge stopped receiving commands.",
  arbiter_changed: "Stopped: the control service restarted.",
  firmware_disarmed: "Stopped: the robot controller disarmed.",
  malformed_packet: "Stopped: the robot controller rejected a malformed packet.",
  invalid_keys: "Stopped: the browser sent an unrecognized key.",
  invalid_browser_message: "Stopped: the browser sent an invalid message.",
  dashboard_shutdown: "Stopped: the dashboard is shutting down.",
  motor_disarmed: "Stopped: the motors disarmed.",
  autonomy_timeout: "Stopped: the simulation navigation source timed out. Wait for readiness, then enable again.",
  search_complete: "Search mission finished. Returned to start and disarmed.",
  search_failed: "Search mission stopped. Check the mission status before trying again.",
};

function send(message) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

function sendKeys() {
  send({ type: "keys", keys: [...heldKeys] });
}

function clearAndStop() {
  heldKeys.clear();
  litKeys.clear();
  renderKeys();
  send({ type: "stop" });
}

function setText(id, value) {
  element(id).textContent = value;
}

function setLamp(lamp, state) {
  if (lamp) lamp.dataset.state = state;
}

function formatAge(ms) {
  if (ms === null || ms === undefined) return "—";
  return ms >= 10000 ? (ms / 1000).toFixed(0) + " s" : ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : ms + " ms";
}

function titleCase(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function renderKeys() {
  for (const cap of document.querySelectorAll(".keycap")) {
    cap.classList.toggle("lit", litKeys.has(cap.dataset.key));
  }
}

function updateVideo(camera) {
  // The live camera process serves annotated MJPEG video on its own port.
  const video = element("camera-video");
  const wanted = camera.video && camera.status !== "offline"
    ? window.location.protocol + "//" + window.location.hostname + ":" + camera.video.port + camera.video.path
    : null;
  if (wanted && video.dataset.src !== wanted) {
    video.dataset.src = wanted;
    video.src = wanted;
  } else if (!wanted && video.dataset.src) {
    delete video.dataset.src;
    video.removeAttribute("src");  // closes the stream connection
  }
  video.hidden = !wanted;
  return Boolean(wanted);
}

function updateCamera(camera) {
  const showingVideo = updateVideo(camera);
  document.body.dataset.camera = camera.status;
  setText("camera-source", "Cam 1 · " + titleCase(camera.backend));
  element("camera-source").dataset.source = camera.backend;
  setText("camera-status", titleCase(camera.status));
  setLamp(document.querySelector('[data-lamp="camera"]'),
    camera.status === "online" ? "ok" : camera.status === "stale" ? "warn" : "off");
  setText("camera-slate-title", camera.status === "stale" ? "Signal stale" : "No signal");
  setText("camera-message", camera.message);

  const count = camera.status === "online" ? camera.detection_count : 0;
  setText("detection-count", count === 0 ? "No people detected" : count + (count === 1 ? " person detected" : " people detected"));
  setText("t-camera", camera.age_ms === null || camera.age_ms === undefined ? "—" : formatAge(camera.age_ms));

  const overlay = element("detection-overlay");
  overlay.replaceChildren();
  // Live video already has boxes drawn on the matching frame by the camera process.
  if (camera.status !== "online" || showingVideo) return;
  for (const detection of camera.detections) {
    const box = document.createElement("div");
    box.className = "detection-box";
    box.style.left = (detection.bbox.x * 100) + "%";
    box.style.top = (detection.bbox.y * 100) + "%";
    box.style.width = (detection.bbox.width * 100) + "%";
    box.style.height = (detection.bbox.height * 100) + "%";
    const label = document.createElement("span");
    label.textContent = detection.label + " " + Math.round(detection.confidence * 100) + "%";
    box.append(label);
    overlay.append(box);
  }
}

// ---------- LiDAR scope ----------

const LIDAR_CAMERA_FOV_DEG = 66;  // AI Camera horizontal field of view, drawn as a wedge
let sensorsMode = "off";
let lidarStatus = "offline";
let lidarScan = null;

function describeBearing(angle) {
  const signed = angle > 180 ? angle - 360 : angle;
  if (Math.abs(signed) <= 10) return "ahead";
  if (Math.abs(signed) >= 170) return "behind";
  return Math.abs(signed) + "° " + (signed > 0 ? "right" : "left");
}

function updateLidarStatus(lidar) {
  lidarStatus = lidar.status;
  const screen = element("lidar-canvas").closest(".aux-screen");
  screen.dataset.status = lidar.status;
  setLamp(document.querySelector('[data-lamp="lidar"]'), lidar.status === "online" ? "ok" : lidar.status === "stale" ? "warn" : "off");
  setText("lidar-status", titleCase(lidar.status));
  setText("lidar-slate-title", lidar.status === "stale" ? "Signal stale" : "No signal");
  setText("lidar-message", lidar.message);
  const nearest = lidar.nearest;
  setText("lidar-nearest", nearest ? "Nearest " + (nearest.mm / 1000).toFixed(2) + " m · " + describeBearing(nearest.angle) : "No obstacles");
}

function updateSensors(sensors) {
  sensorsMode = sensors.mode;
  document.body.dataset.sensors = sensors.mode;
  if (sensors.mode !== "off") updateLidarStatus(sensors.lidar);
}

function drawLidar() {
  const canvas = element("lidar-canvas");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = rect.width, h = rect.height, cx = w / 2, cy = h / 2;
  ctx.clearRect(0, 0, w, h);
  if (!lidarScan || !lidarScan.bins) return;

  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--ink").trim();
  const ink3 = css.getPropertyValue("--ink-3").trim();
  const line = css.getPropertyValue("--desk-line").trim();

  // Scale so most readings fit: the 90th-percentile distance, rounded up to whole meters.
  const readings = lidarScan.bins.filter((mm) => mm > 0).sort((a, b) => a - b);
  const p90 = readings.length ? readings[Math.floor(readings.length * 0.9)] : 2000;
  const rangeM = Math.min(8, Math.max(2, Math.ceil(p90 / 1000)));
  const radius = Math.min(w, h) / 2 - 14;
  const pxPerMm = radius / (rangeM * 1000);
  const ringStep = rangeM > 4 ? 2 : 1;
  const toXY = (angleDeg, mm) => {
    const a = (angleDeg * Math.PI) / 180;
    return [cx + Math.sin(a) * mm * pxPerMm, cy - Math.cos(a) * mm * pxPerMm];
  };

  // AI Camera field of view, pointing forward.
  ctx.fillStyle = "oklch(1 0 0 / 0.05)";
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.arc(cx, cy, radius, -Math.PI / 2 - (LIDAR_CAMERA_FOV_DEG / 2) * Math.PI / 180, -Math.PI / 2 + (LIDAR_CAMERA_FOV_DEG / 2) * Math.PI / 180);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = line;
  ctx.lineWidth = 1;
  for (let m = ringStep; m <= rangeM; m += ringStep) {
    ctx.beginPath();
    ctx.arc(cx, cy, m * 1000 * pxPerMm, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.moveTo(cx, cy - radius); ctx.lineTo(cx, cy + radius);
  ctx.moveTo(cx - radius, cy); ctx.lineTo(cx + radius, cy);
  ctx.stroke();

  ctx.fillStyle = ink;
  lidarScan.bins.forEach((mm, angle) => {
    if (mm <= 0 || mm > rangeM * 1000) return;
    const [x, y] = toXY(angle + 0.5, mm);
    ctx.fillRect(x - 1.25, y - 1.25, 2.5, 2.5);
  });

  // The robot, about 30 cm x 25 cm, with a notch marking the front (camera end).
  const rw = Math.max(10, 250 * pxPerMm), rh = Math.max(12, 300 * pxPerMm);
  ctx.fillStyle = ink3;
  ctx.fillRect(cx - rw / 2, cy - rh / 2, rw, rh);
  ctx.fillStyle = ink;
  ctx.beginPath();
  ctx.moveTo(cx, cy - rh / 2 - 6);
  ctx.lineTo(cx - 5, cy - rh / 2);
  ctx.lineTo(cx + 5, cy - rh / 2);
  ctx.closePath();
  ctx.fill();

  const nearest = lidarScan.nearest;
  if (nearest && nearest.mm <= rangeM * 1000) {
    const [x, y] = toXY(nearest.angle + 0.5, nearest.mm);
    ctx.strokeStyle = ink;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.stroke();
  }
  setText("lidar-scale", "Rings " + ringStep + " m");
}

// Live SLAM map, relayed from the autonomy stack's map viewer. Drawn in world
// coordinates, centered on the robot, so the view follows it as it moves.
const MAP_ZOOMS_M = [1.5, 2, 3, 5, 8, 12];  // meters across the shorter side
let mapInfo = null;
let mapImage = null;  // recolored offscreen canvas, one pixel per map cell
let mapImageAt = 0;
let mapZoomIndex = 2;
let mapHeadingUp = true;
try {
  const saved = JSON.parse(window.localStorage.getItem("rescuebot-map-view") || "null");
  if (saved && MAP_ZOOMS_M[saved.zoom] !== undefined) mapZoomIndex = saved.zoom;
  if (saved && typeof saved.headingUp === "boolean") mapHeadingUp = saved.headingUp;
} catch (_error) {
  // View preferences are a convenience only.
}

function saveMapView() {
  try {
    window.localStorage.setItem("rescuebot-map-view", JSON.stringify({ zoom: mapZoomIndex, headingUp: mapHeadingUp }));
  } catch (_error) {
    // Ignored: private windows may block storage.
  }
}

function recolorMap(image) {
  // map_viewer colors: unknown 205 grey, free white, occupied darker greys,
  // robot red, people blue. Unknown stays transparent (the screen shows
  // through); the robot and people are drawn on top instead.
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(image, 0, 0);
  const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const d = pixels.data;
  for (let i = 0; i < d.length; i += 4) {
    const r = d[i], g = d[i + 1], b = d[i + 2];
    if (r === 205 && g === 205 && b === 205) {
      d[i + 3] = 0;                                           // unknown
    } else if ((r === g && g === b && r >= 250) || r !== g || g !== b) {
      d[i] = 255; d[i + 1] = 255; d[i + 2] = 255; d[i + 3] = 34;  // free (and baked-in markers)
    } else {
      const occupied = 1 - r / 254;                            // 0 free .. 1 wall
      d[i] = 240; d[i + 1] = 236; d[i + 2] = 226; d[i + 3] = Math.round(90 + 165 * occupied);
    }
  }
  ctx.putImageData(pixels, 0, 0);
  return canvas;
}

function drawMap() {
  const canvas = element("map-canvas");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = rect.width, h = rect.height;
  ctx.clearRect(0, 0, w, h);
  const info = mapInfo;
  if (!info || !info.width || !mapImage) return;

  const res = info.resolution;
  const robot = info.robot || {
    x: info.origin.x + info.width * res / 2, y: info.origin.y + info.height * res / 2, yaw: Math.PI / 2,
  };
  const ppm = Math.min(w, h) / MAP_ZOOMS_M[mapZoomIndex];
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--ink").trim();
  const red = css.getPropertyValue("--tally-red").trim();
  const blue = "oklch(0.7 0.14 250)";

  // World frame: x right, y up, centered on the robot; heading-up rotates
  // the world so the robot's front points to the top of the screen.
  ctx.save();
  ctx.translate(w / 2, h / 2);
  if (mapHeadingUp) ctx.rotate(robot.yaw - Math.PI / 2);
  ctx.scale(ppm, -ppm);
  ctx.translate(-robot.x, -robot.y);
  const world = ctx.getTransform();

  ctx.save();
  ctx.translate(info.origin.x, info.origin.y + info.height * res);
  ctx.scale(res, -res);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(mapImage, 0, 0);
  ctx.restore();

  // People the camera saw in the last minute.
  ctx.fillStyle = blue;
  for (const person of info.people || []) {
    ctx.beginPath();
    ctx.arc(person.x, person.y, 0.12, 0, Math.PI * 2);
    ctx.fill();
  }

  // The robot: about 30 x 25 cm, pointing along its heading.
  ctx.translate(robot.x, robot.y);
  ctx.rotate(robot.yaw);
  ctx.fillStyle = red;
  ctx.beginPath();
  ctx.moveTo(0.2, 0);
  ctx.lineTo(-0.15, 0.13);
  ctx.lineTo(-0.08, 0);
  ctx.lineTo(-0.15, -0.13);
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  // Numbered people from the room search, labelled in screen space so the
  // text stays upright whatever the map rotation.
  const report = currentState?.search_report;
  ctx.font = "600 12px " + css.getPropertyValue("--sans").trim();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const person of report?.people || []) {
    const p = world.transformPoint(new DOMPoint(person.x, person.y));
    const x = p.x / dpr, y = p.y / dpr;
    ctx.fillStyle = blue;
    ctx.beginPath();
    ctx.arc(x, y, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = ink;
    ctx.fillText(String(person.id), x, y + 0.5);
  }
}

async function refreshMap() {
  const screen = element("map-canvas").closest(".aux-screen");
  try {
    const response = await fetch("/api/map.json", { cache: "no-store" });
    const info = response.ok ? await response.json() : null;
    if (!info || !info.width) throw new Error("no map");
    mapInfo = info;
    // The image changes at most every map update (~2 s); the pose every call.
    if (!mapImage || Date.now() - mapImageAt > 1000) {
      mapImageAt = Date.now();
      const image = new Image();
      image.onload = () => { mapImage = recolorMap(image); drawMap(); };
      image.src = "/api/map.png?t=" + mapImageAt;
    }
    screen.dataset.status = "online";
    setLamp(document.querySelector('[data-lamp="map"]'), info.robot ? "ok" : "warn");
    setText("map-status", info.robot ? "Live" : "No pose");
    const view = MAP_ZOOMS_M[mapZoomIndex] + " m view";
    setText("map-pose", info.robot
      ? `x ${info.robot.x.toFixed(2)} m · y ${info.robot.y.toFixed(2)} m · ${view}`
      : `Robot pose unknown · ${view}`);
  } catch (_error) {
    mapInfo = null;
    screen.dataset.status = "offline";
    setLamp(document.querySelector('[data-lamp="map"]'), "off");
    setText("map-status", "Offline");
  }
  drawMap();
}

async function refreshLidar() {
  if (sensorsMode === "off" || lidarStatus === "offline") {
    if (lidarScan) { lidarScan = null; drawLidar(); }
    return;
  }
  try {
    const response = await fetch("/api/lidar", { cache: "no-store" });
    if (!response.ok) return;
    lidarScan = await response.json();
    drawLidar();
  } catch (_error) {
    // LiDAR display only; driving never depends on it.
  }
}

function driveState(control) {
  if (control.arming) return "arming";
  return control.armed ? "armed" : "disabled";
}

function reasonText(control, drive) {
  if (control.fault) {
    return { text: reasons[control.fault] || "Stopped.", code: control.fault };
  }
  if (!connected) return { text: "Reconnecting to the dashboard. Driving stays disabled." };
  if (!canControl) {
    return { text: control.armed
      ? "Another browser is driving. You can still stop the robot."
      : "Another browser has control. You can still stop the robot." };
  }
  if (drive === "arming") return { text: "Waiting for the robot controller to confirm arming." };
  if (drive === "armed") return { text: "Driving. Hold keys to move, release to stop. Space stops." };
  return { text: "Release all keys, then enable driving." };
}

function updateChain(data) {
  const { control, motor } = data;
  const autonomy = data.autonomy || { available: false, active: false };
  const bridge = motor.backend === "bridge";

  setLamp(document.querySelector('[data-node="browser"] .lamp'), connected ? "ok" : "fault");
  setText("node-browser", connected ? "Connected" : "Disconnected");

  setLamp(document.querySelector('[data-node="control"] .lamp'),
    control.armed ? (control.arming ? "warn" : "ok") : control.owner_session ? "off" : "off");
  setText("node-control", !control.owner_session ? "No owner" : canControl ? "You · " + (control.armed ? "armed" : "disarmed") : "Other browser");

  setText("node-motor-name", bridge ? "Motor bridge" : motor.backend === "gazebo" ? "Gazebo bridge" : "Motor backend");
  if (bridge) {
    setLamp(document.querySelector('[data-node="motor"] .lamp'), motor.healthy ? "ok" : "fault");
    setText("node-motor", motor.healthy ? "Connected · " + formatAge(motor.status_age_ms) : "Unavailable");
    setLamp(document.querySelector('[data-node="firmware"] .lamp'),
      motor.firmware_armed ? "ok" : motor.transport_connected ? "off" : "fault");
    setText("node-firmware", !motor.transport_connected ? "No link" : motor.firmware_armed ? "Armed" : "Disarmed");
  } else {
    setLamp(document.querySelector('[data-node="motor"] .lamp'), motor.healthy ? "ok" : "fault");
    setText("node-motor", motor.backend === "gazebo" ? "Simulation · local IPC" : "Mock · in-process");
    setLamp(document.querySelector('[data-node="firmware"] .lamp'), "off");
    setText("node-firmware", motor.backend === "gazebo" ? "Not used (simulation)" : "Not connected (mock)");
  }

  setText("t-ack", bridge ? formatAge(motor.ack_age_ms) : "—");
  setText("t-status", bridge ? formatAge(motor.status_age_ms) : "—");
  setText("t-browser", formatAge(control.browser_age_ms));
  const imu = bridge ? motor.imu : null;
  setText("t-imu", imu && imu.available && imu.heading !== null ? imu.heading.toFixed(1) + "° · cal " + imu.calibration : "—");
  setText("t-dropped", bridge ? String(motor.dropped_commands) : "—");
}

// Buttons show the state the firmware (or mock) confirmed, not the request.
function updateAccessories(motor) {
  const accessories = motor.accessories || { buzzer: false, light: false, available: false };
  for (const button of document.querySelectorAll(".accessory")) {
    const on = Boolean(accessories[button.dataset.accessory]);
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.querySelector(".accessory-state").textContent = on ? "On" : "Off";
    button.disabled = !connected || !canControl || !accessories.available;
  }
}

const urgencyText = { none: "No one in danger", low: "Low urgency", medium: "May need help", high: "Needs help now" };
let geminiImageKey = null;

const GEMINI_COUNT_MAX_AGE_S = 45;

function uniquePeopleText(gemini, camera) {
  // Gemini's unique count replaces the raw box count while its answer is recent.
  // Gemini may count someone the detector missed. Fewer boxes than Gemini saw
  // means people left (cap at the boxes); more means a new assessment is coming.
  const latest = gemini?.latest;
  const boxes = camera.status === "online" ? camera.detection_count : 0;
  if (!latest || boxes === 0 || latest.age_s > GEMINI_COUNT_MAX_AGE_S) return null;
  if (boxes > latest.detector_people) {
    return boxes + (boxes === 1 ? " person" : " people") + " detected · Gemini checking…";
  }
  const unique = boxes < latest.detector_people ? Math.min(latest.unique_people, boxes) : latest.unique_people;
  let text = unique + " unique " + (unique === 1 ? "person" : "people") + " on screen";
  if (unique !== boxes) text += " · detector: " + boxes + (boxes === 1 ? " box" : " boxes");
  return text;
}

function updateGemini(gemini, camera) {
  const panel = element("gemini-panel");
  panel.hidden = !gemini?.enabled;
  if (!gemini?.enabled) return;
  const unique = uniquePeopleText(gemini, camera);
  if (unique) setText("detection-count", unique);
  panel.dataset.state = gemini.state;
  setText("gemini-status", gemini.message);
  element("gemini-button").disabled = !connected || !gemini.can_ask;
  element("gemini-button").textContent = gemini.state === "thinking" ? "Asking Gemini…" : "Ask Gemini now";
  const latest = gemini.latest;
  element("gemini-result").hidden = !latest;
  const badge = element("gemini-urgency");
  badge.dataset.urgency = latest ? latest.urgency : "none";
  badge.textContent = latest ? urgencyText[latest.urgency] || latest.urgency : "No assessment";
  if (latest) {
    setText("gemini-summary", latest.summary);
    setText("gemini-action", latest.recommended_action || "—");
    setText("gemini-people", String(latest.unique_people) + (latest.people_note ? " · " + latest.people_note : ""));
    setText("gemini-posture", titleCase(latest.posture));
    setText("gemini-hazards", latest.hazards.length ? latest.hazards.join(", ") : "None seen");
    setText("gemini-time", latest.time.slice(11) + " · " + (latest.latency_ms / 1000).toFixed(1) + " s · " + latest.model);
    const key = latest.time + "|" + gemini.calls;
    if (key !== geminiImageKey) {
      geminiImageKey = key;
      element("gemini-image").src = "/api/gemini/snapshot.jpg?k=" + encodeURIComponent(key);
    }
  }
  const history = element("gemini-history");
  history.replaceChildren(...gemini.history.map((item) => {
    const li = document.createElement("li");
    li.textContent = item.time.slice(11) + " · " + item.unique_people + " unique · " + (urgencyText[item.urgency] || item.urgency) + " · " + item.summary;
    li.title = item.summary;
    return li;
  }));
}

function updateDashboard(data) {
  currentState = data;
  const { control, motor, camera } = data;
  const autonomy = data.autonomy || { available: false, active: false };
  canControl = data.can_control;
  const drive = driveState(control);

  document.body.dataset.drive = drive;
  document.body.dataset.link = connected ? "online" : "offline";
  document.body.dataset.fault = control.fault ? "true" : "false";
  document.body.dataset.control = canControl ? "owner" : "viewer";

  setText("driving-status", drive === "arming" ? "Arming…" : drive === "armed" ? "Armed · driving" : "Disabled");

  const reason = reasonText(control, drive);
  if (autonomy.active && connected && canControl && drive === "armed") {
    reason.text = "Autonomy enabled. Send a goal below. W/A/S/D take over; Space stops.";
  }
  const message = element("fault-message");
  message.textContent = reason.text;
  if (reason.code) {
    const code = document.createElement("code");
    code.textContent = reason.code;
    message.append(code);
  }

  setText("speed-value", control.speed_percent + "%");
  setText("speed-limit", control.speed_limit + " / 255 PWM");

  updateCamera(camera);
  updateChain(data);
  if (data.sensors) updateSensors(data.sensors);
  updateGemini(data.gemini, camera);

  for (const wheel of ["fl", "fr", "rl", "rr"]) {
    const value = motor.wheels[wheel];
    const cell = element("wheel-" + wheel);
    cell.textContent = value > 0 ? "+" + value : String(value);
    cell.classList.toggle("moving", value !== 0);
  }
  setText(
    "wheel-caption",
    motor.backend === "bridge" ? "Wheel PWM · acknowledged by firmware" : "Wheel PWM · requested (mock)"
  );

  element("enable-button").disabled = !connected || !canControl || heldKeys.size > 0 || drive !== "disabled";
  element("enable-button").textContent = !canControl ? "Read-only"
    : drive === "armed" ? "Driving enabled" : drive === "arming" ? "Arming…" : "Enable driving";
  element("stop-button").disabled = !connected;
  updateAccessories(motor);
  const autonomyButton = element("autonomy-button");
  autonomyButton.hidden = !autonomy.available;
  autonomyButton.disabled = !connected || !canControl || drive !== "armed" || heldKeys.size > 0 || autonomy.active;
  autonomyButton.textContent = autonomy.active ? "Autonomy active" : "Start autonomy";
  updateNavigation(autonomy, drive);
  updateSearchReport(data.search_report);
  const playback = data.simulation_playback;
  element("simulation-playback").hidden = !playback;
  if (playback) {
    if (playback.pending) element("enable-button").disabled = true;
    for (const button of document.querySelectorAll("[data-playback-rate]")) {
      button.disabled = !connected || !canControl || drive !== "disabled" || playback.pending || heldKeys.size > 0;
      button.setAttribute("aria-pressed", String(Number(button.dataset.playbackRate) === playback.target));
    }
    const actual = playback.actual === null ? "Clock unavailable" : `Actual ${playback.actual.toFixed(2)}×${playback.paused ? " (paused)" : ""}`;
    const target = playback.target === null ? "Choose a playback rate" : `Requested ${playback.target}×`;
    setText("playback-status", playback.pending ? "Applying playback rate…" : `${target} · ${actual}`);
    if (playback.error) setText("playback-feedback", playback.error);
  }
}

function navigationGoalInput() {
  const inputs = [element("goal-forward"), element("goal-right")];
  const [forward, right] = inputs.map((input) => input.valueAsNumber);
  const distance = Math.hypot(forward, right);
  let error = "";
  if (!Number.isFinite(distance)) error = "Enter both Forward and Right distances.";
  else if (distance > 2) error = `Combined distance is ${distance.toFixed(2)} m — maximum is 2 m. Reduce Forward or Right.`;
  else if (distance < 0.1) error = `Combined distance is ${distance.toFixed(2)} m — minimum is 0.1 m.`;
  else if (inputs.some((input) => input.validity.stepMismatch)) error = "Enter distances in steps of 0.1 m.";
  return { forward, right, distance, error };
}

function updateSearchReport(report) {
  // Physical room search: one entry per distinct person, with Gemini's notes.
  element("search-results").hidden = !report || (report.phase === "idle" && report.count === 0);
  if (!report) return;
  const done = report.phase === "complete";
  setText("search-count", report.count === 0 ? (done ? "No people found" : "No people found yet")
    : `${report.count} ${report.count === 1 ? "person" : "people"} found${done ? "" : " so far"}`);
  element("search-people").replaceChildren(...report.people.map((person) => {
    const li = document.createElement("li");
    const note = person.gemini;
    const where = `x ${person.x.toFixed(1)} m, y ${person.y.toFixed(1)} m`;
    li.textContent = `Person ${person.id} · ${where} · seen ${person.sightings}×`
      + (note ? ` · ${urgencyText[note.urgency] || note.urgency} · ${titleCase(note.posture || "unknown")} · ${note.summary}` : " · Gemini: no assessment yet");
    if (note) li.dataset.urgency = note.urgency;
    return li;
  }));
}

function updateNavigation(autonomy, drive) {
  element("simulation-navigation").hidden = !autonomy.available;
  if (!autonomy.available) return;
  element("navigation-title").textContent = autonomy.physical ? "Robot autonomy" : "Simulation autonomy";
  if (autonomy.physical) {
    setText("navigation-description", "Enable driving, start autonomy, then send a nearby goal. The robot keeps its heading: negative forward reverses, right/left strafe (strafing is slower on this robot).");
  }
  setText("autonomy-speed-note", autonomy.speed_percent ? `Autonomy motor speed: up to ${autonomy.speed_percent}%.` : "");
  const nav = autonomy.navigation || { ready: false, reason: "Waiting for navigation" };
  const currentMission = autonomy.active && nav.active && nav.mission === autonomy.mission;
  const search = nav.search;
  const searching = search && ["exploring", "notifying", "return_pending", "returning"].includes(search.phase);
  const busy = currentMission && (searching || nav.pending || ["pending", "executing"].includes(nav.goal_state));
  const labels = {
    idle: "Ready for a goal", pending: "Sending goal…", executing: "Navigating to goal",
    succeeded: "Goal reached — choose another", canceled: "Goal canceled",
    aborted: "Goal could not be reached — choose another", failed: "Navigation failed — choose another",
    rejected: "Goal rejected — choose another",
  };
  const status = !connected ? "Dashboard disconnected" : !nav.ready ? nav.reason
    : currentMission ? (searching ? "Search mission active — progress below" : nav.pending ? labels.pending : labels[nav.goal_state])
    : autonomy.active ? "Starting mission…"
    : drive === "armed" ? "Driving enabled — click Start autonomy, then Send goal"
    : "Ready — enable driving, then Start autonomy again";
  setText("navigation-status", status);
  const goal = navigationGoalInput();
  setText("goal-distance", goal.error || `Combined distance: ${goal.distance.toFixed(2)} m (allowed: 0.1–2 m).`);
  element("goal-distance").dataset.invalid = String(Boolean(goal.error));
  element("autonomy-button").disabled ||= !nav.ready;
  element("send-goal-button").disabled = !connected || !canControl || drive !== "armed"
    || heldKeys.size > 0 || !nav.ready || !currentMission || busy || Boolean(goal.error);
  for (const id of ["goal-forward", "goal-right"]) element(id).disabled = busy || !canControl;
  const pose = nav.pose;
  setText("navigation-pose", pose
    ? `SLAM position: x ${pose.x.toFixed(2)} m · y ${pose.y.toFixed(2)} m`
    : "Map position unavailable");
  if (nav.request_error && currentMission) setText("navigation-feedback", nav.request_error);
  element("search-mission").hidden = !search || (!search.available && search.phase === "idle");
  element("search-button").disabled = !connected || !canControl || drive !== "armed"
    || heldKeys.size > 0 || !nav.ready || !currentMission || busy || !search?.available;
  const phases = {
    idle: "Enable driving → Start autonomy → Search for person & return",
    exploring: "Searching for a person marker…", notifying: "Simulated person found — stopping to report",
    return_pending: "Search ended — preparing to return", returning: "Returning to the saved starting position…",
    complete: search?.reason, failed: search?.reason, canceled: "Search canceled. Start a new mission to try again.",
  };
  if (autonomy.physical) {
    element("search-button").textContent = "Search room & return";
    setText("search-description", "Cover the whole room, count each person the camera finds (Gemini describes them), then return to this mission’s starting position.");
    Object.assign(phases, {
      idle: "Enable driving → Start autonomy → Search room & return",
      exploring: "Searching the room…", return_pending: "Room searched — preparing to return",
    });
  }
  setText("search-status", phases[search?.phase] || "Search world unavailable");
  element("search-notification").hidden = autonomy.physical || !search?.found;
  if (!autonomy.physical && search?.found && search.target) {
    const notice = `SIMULATION: person marker found at x ${search.target.x.toFixed(2)} m, y ${search.target.y.toFixed(2)} m. ${search.phase === "complete" ? "Returned to start." : ""}`;
    if (element("search-notification").textContent !== notice) setText("search-notification", notice);
  }
}

async function refreshState() {
  // Keeps every readout fresh while idle or read-only, when no WebSocket
  // replies arrive. Ownership stays whatever this browser's own socket
  // reported: the HTTP poll cannot know which session is asking.
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    if (data.control && data.motor && data.camera) {
      updateDashboard({ ...data, can_control: canControl });
    } else if (data.camera) {
      updateCamera(data.camera);
    }
  } catch (_error) {
    // The WebSocket control path retains its own safety handling.
  }
}

function connect() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(scheme + "://" + window.location.host + "/ws/control");

  socket.addEventListener("open", () => {
    connected = true;
    send({ type: "claim" });
  });

  socket.addEventListener("close", () => {
    connected = false;
    canControl = false;
    heldKeys.clear();
    litKeys.clear();
    renderKeys();
    if (currentState) updateDashboard(currentState);
    window.setTimeout(connect, 1000);
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") updateDashboard(message.data);
    if (message.type === "simulation_playback") {
      setText("playback-feedback", message.accepted ? "" : "Playback change not accepted. Stop driving and wait for any pending change.");
    }
    if (message.type === "navigation_goal") {
      setText("navigation-feedback", message.accepted ? "" : "Goal not accepted. Check readiness and wait for the current goal to finish.");
    }
    if (message.type === "start_search") {
      setText("navigation-feedback", message.accepted ? "" : "Search not accepted. Start autonomy and wait for the current goal to finish.");
    }
    if (message.type === "start_autonomy" && currentState?.autonomy.available) {
      setText("navigation-feedback", message.accepted ? "" : "Start was not accepted. Release keys, enable driving, and wait for navigation readiness.");
    }
  });
}

window.addEventListener("keydown", (event) => {
  // Simulation fields keep number editing and Select All browser shortcuts.
  // W/A/S/D still take over immediately, and Space always reaches Stop.
  if (currentState?.autonomy.available && event.target.closest?.("#navigation-form input")
      && (event.code.startsWith("Arrow")
        || (event.code === "KeyA" && (event.metaKey || event.ctrlKey)))) return;
  if (displayKeys.has(event.code)) {
    litKeys.add(event.code);
    renderKeys();
  }

  if (movementKeys.has(event.code)) {
    event.preventDefault();
    if (!event.repeat) heldKeys.add(event.code);
    sendKeys();
    if (currentState) updateDashboard(currentState);
    return;
  }

  if (event.code === "ArrowUp" || event.code === "ArrowDown") {
    event.preventDefault();
    if (!event.repeat) send({ type: "speed", delta: event.code === "ArrowUp" ? 10 : -10 });
    return;
  }

  if (event.code === "Space") {
    event.preventDefault();
    clearAndStop();
  }
});

window.addEventListener("keyup", (event) => {
  if (displayKeys.has(event.code)) {
    litKeys.delete(event.code);
    renderKeys();
  }
  if (!movementKeys.has(event.code)) return;
  event.preventDefault();
  heldKeys.delete(event.code);
  sendKeys();
});

window.addEventListener("blur", clearAndStop);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearAndStop();
});

element("enable-button").addEventListener("click", () => send({ type: "enable" }));
element("autonomy-button").addEventListener("click", () => send({ type: "start_autonomy" }));
element("search-button").addEventListener("click", () => send({ type: "start_search" }));
element("stop-button").addEventListener("click", clearAndStop);
element("gemini-button").addEventListener("click", () => {
  element("gemini-button").disabled = true;
  send({ type: "gemini_assess" });
});
for (const button of document.querySelectorAll(".accessory")) {
  button.addEventListener("click", () => send({
    type: "accessory",
    name: button.dataset.accessory,
    on: button.getAttribute("aria-pressed") !== "true",
  }));
}
for (const button of document.querySelectorAll("[data-playback-rate]")) {
  button.addEventListener("click", () => send({ type: "simulation_playback", rate: Number(button.dataset.playbackRate) }));
}
element("navigation-form").addEventListener("input", () => {
  if (!currentState?.autonomy.available) return;
  setText("navigation-feedback", "");
  updateNavigation(currentState.autonomy, driveState(currentState.control));
});
element("navigation-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (element("send-goal-button").disabled) return;
  const { forward, right, error } = navigationGoalInput();
  if (error) {
    setText("navigation-feedback", error);
    return;
  }
  setText("navigation-feedback", "Sending goal…");
  element("send-goal-button").disabled = true;
  send({ type: "navigation_goal", forward, right });
});

window.setInterval(() => {
  if (connected && currentState?.control.armed) sendKeys();
}, 50);
window.setInterval(refreshState, 100);
window.setInterval(refreshLidar, 200);
window.setInterval(refreshMap, 500);
window.addEventListener("resize", drawLidar);
window.addEventListener("resize", drawMap);
element("map-zoom-in").addEventListener("click", () => {
  mapZoomIndex = Math.max(0, mapZoomIndex - 1);
  saveMapView();
  refreshMap();
});
element("map-zoom-out").addEventListener("click", () => {
  mapZoomIndex = Math.min(MAP_ZOOMS_M.length - 1, mapZoomIndex + 1);
  saveMapView();
  refreshMap();
});
element("map-orientation").addEventListener("click", () => {
  mapHeadingUp = !mapHeadingUp;
  element("map-orientation").textContent = mapHeadingUp ? "Heading up" : "North up";
  element("map-orientation").setAttribute("aria-pressed", String(mapHeadingUp));
  saveMapView();
  drawMap();
});
element("map-orientation").textContent = mapHeadingUp ? "Heading up" : "North up";
element("map-orientation").setAttribute("aria-pressed", String(mapHeadingUp));

connect();
