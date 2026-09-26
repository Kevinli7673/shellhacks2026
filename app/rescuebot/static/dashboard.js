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
  const autonomyButton = element("autonomy-button");
  autonomyButton.hidden = !autonomy.available;
  autonomyButton.disabled = !connected || !canControl || drive !== "armed" || heldKeys.size > 0 || autonomy.active;
  autonomyButton.textContent = autonomy.active ? "Autonomy active" : "Start autonomy";
  updateNavigation(autonomy, drive);
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

function updateNavigation(autonomy, drive) {
  element("simulation-navigation").hidden = !autonomy.available;
  if (!autonomy.available) return;
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
  setText("search-status", phases[search?.phase] || "Search world unavailable");
  element("search-notification").hidden = !search?.found;
  if (search?.found && search.target) {
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

connect();
