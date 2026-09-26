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

async function refreshCameraState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    if (data.camera) updateCamera(data.camera);
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
window.setInterval(refreshCameraState, 100);

connect();
