const movementKeys = new Set(["KeyW", "KeyS", "KeyA", "KeyD", "ArrowLeft", "ArrowRight"]);
const heldKeys = new Set();
let socket;
let connected = false;
let canControl = false;
let currentState;

const element = (id) => document.getElementById(id);

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
  send({ type: "stop" });
}

function setText(id, value) {
  element(id).textContent = value;
}

function updateDashboard(data) {
  currentState = data;
  const { control, motor, camera } = data;
  canControl = data.can_control;

  const connection = element("connection-status");
  connection.className = "pill " + (connected ? "online" : "offline");
  connection.textContent = connected ? "Connected" : "Disconnected";

  setText("ownership-status", canControl ? "Control available" : "Read-only");
  setText("driving-status", control.armed ? "Enabled" : "Disabled");
  setText("speed-value", control.speed_percent + "%");
  setText("speed-limit", motor.wheels ? control.speed_limit + " / 255 PWM" : "0 / 255 PWM");
  setText("fault-message", control.fault || (canControl ? "Click Enable Driving to arm controls." : "Another browser owns driving."));
  setText("camera-status", "Camera " + camera.backend + " is " + camera.status);
  setText("camera-message", camera.message);
  setText("wheel-fl", motor.wheels.fl);
  setText("wheel-fr", motor.wheels.fr);
  setText("wheel-rl", motor.wheels.rl);
  setText("wheel-rr", motor.wheels.rr);

  element("enable-button").disabled = !connected || !canControl || heldKeys.size > 0;
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
    if (currentState) updateDashboard(currentState);
    window.setTimeout(connect, 1000);
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") updateDashboard(message.data);
  });
}

window.addEventListener("keydown", (event) => {
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

connect();
