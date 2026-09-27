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
  const bridge = motor.backend === "bridge";

  setLamp(document.querySelector('[data-node="browser"] .lamp'), connected ? "ok" : "fault");
  setText("node-browser", connected ? "Connected" : "Disconnected");

  setLamp(document.querySelector('[data-node="control"] .lamp'),
    control.armed ? (control.arming ? "warn" : "ok") : control.owner_session ? "off" : "off");
  setText("node-control", !control.owner_session ? "No owner" : canControl ? "You · " + (control.armed ? "armed" : "disarmed") : "Other browser");

  setText("node-motor-name", bridge ? "Motor bridge" : "Motor backend");
  if (bridge) {
    setLamp(document.querySelector('[data-node="motor"] .lamp'), motor.healthy ? "ok" : "fault");
    setText("node-motor", motor.healthy ? "Connected · " + formatAge(motor.status_age_ms) : "Unavailable");
    setLamp(document.querySelector('[data-node="firmware"] .lamp'),
      motor.firmware_armed ? "ok" : motor.transport_connected ? "off" : "fault");
    setText("node-firmware", !motor.transport_connected ? "No link" : motor.firmware_armed ? "Armed" : "Disarmed");
  } else {
    setLamp(document.querySelector('[data-node="motor"] .lamp'), motor.healthy ? "ok" : "fault");
    setText("node-motor", "Mock · in-process");
    setLamp(document.querySelector('[data-node="firmware"] .lamp'), "off");
    setText("node-firmware", "Not connected (mock)");
  }

  setText("t-ack", bridge ? formatAge(motor.ack_age_ms) : "—");
  setText("t-status", bridge ? formatAge(motor.status_age_ms) : "—");
  setText("t-browser", formatAge(control.browser_age_ms));
  const imu = bridge ? motor.imu : null;
  setText("t-imu", imu && imu.available && imu.heading !== null ? imu.heading.toFixed(1) + "° · cal " + imu.calibration : "—");
  setText("t-dropped", bridge ? String(motor.dropped_commands) : "—");
}

function updateDashboard(data) {
  currentState = data;
  const { control, motor, camera } = data;
  canControl = data.can_control;
  const drive = driveState(control);

  document.body.dataset.drive = drive;
  document.body.dataset.link = connected ? "online" : "offline";
  document.body.dataset.fault = control.fault ? "true" : "false";
  document.body.dataset.control = canControl ? "owner" : "viewer";

  setText("driving-status", drive === "arming" ? "Arming…" : drive === "armed" ? "Armed · driving" : "Disabled");

  const reason = reasonText(control, drive);
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
  });
}

window.addEventListener("keydown", (event) => {
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
element("stop-button").addEventListener("click", clearAndStop);

window.setInterval(() => {
  if (connected && currentState?.control.armed) sendKeys();
}, 50);
window.setInterval(refreshState, 100);
window.setInterval(refreshLidar, 200);
window.addEventListener("resize", drawLidar);

connect();
