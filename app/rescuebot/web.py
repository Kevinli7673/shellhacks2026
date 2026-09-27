"""FastAPI dashboard transport for the manual-control service."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
import asyncio
import argparse
import os
from pathlib import Path
import secrets
import threading
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .accessory_auto import AccessoryAutomation
from .bridge_backend import BridgeMotorBackend
from .autonomy import DEFAULT_AUTONOMY_MIN_PWM, DEFAULT_AUTONOMY_SPEED_PERCENT
from .autonomy_ipc import AutonomyHostEndpoint
from .gemini import (
    GeminiTriage,
    api_key_from_env,
    call_gemini,
    fetch_snapshot,
    models_from_text,
)
from .gazebo_backend import GazeboMotorBackend, default_sim_command_socket
from .live_camera import (
    DEFAULT_DETECTOR,
    DEFAULT_VIDEO_PORT,
    LiveCameraBackend,
    detector_args_from_env,
)
from .motor_bridge import default_run_dir
from .navigation_ipc import NavigationHostEndpoint
from .replay_camera import MockCameraBackend, ReplayCameraBackend
from .sensors import OffSensors, Sensors, create_sensors
from .service import ACCESSORY_NAMES, RobotControlService
from .simulation_playback import SimulationPlayback
from .voice import STARTUP_PHRASE, CameraVoice, Speaker, alert_text


STATIC_DIR = Path(__file__).with_name("static")
CONTROL_TICK_SECONDS = 0.05


async def _control_loop(service: RobotControlService) -> None:
    while True:
        service.tick()
        await asyncio.sleep(CONTROL_TICK_SECONDS)


def _dashboard_state(
    service: RobotControlService,
    session: str | None,
    camera: MockCameraBackend | ReplayCameraBackend | LiveCameraBackend | _LockedCamera,
    sensors: Sensors | None = None,
    playback: SimulationPlayback | None = None,
    gemini: GeminiTriage | None = None,
) -> dict[str, object]:
    state = service.state()
    if playback is not None:
        state["simulation_playback"] = playback.state()
    state["camera"] = camera.status()
    state["sensors"] = (sensors or OffSensors()).status()
    state["gemini"] = gemini.status() if gemini is not None else {"enabled": False}
    control = state["control"]
    assert isinstance(control, dict)
    return {
        "session": session,
        "can_control": control["owner_session"] in (None, session),
        **state,
    }


class _LockedCamera:
    """Serializes camera status reads between the web handlers and the voice thread."""

    def __init__(self, camera: MockCameraBackend | ReplayCameraBackend | LiveCameraBackend) -> None:
        self._camera = camera
        self._lock = threading.Lock()

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._camera.status()


def _create_camera_voice(camera: _LockedCamera) -> CameraVoice:
    if not os.environ.get("ELEVENLABS_API_KEY"):
        print("Warning: ELEVENLABS_API_KEY isn't set; only cached phrases use ElevenLabs.", flush=True)
    speaker = Speaker()
    speaker.say(STARTUP_PHRASE)
    speaker.prepare(
        [alert_text([{"label": "person", "bearing_deg": bearing}]) for bearing in (0, 20, -20)]
    )
    return CameraVoice(camera.status, speaker)


def _create_gemini(camera: _LockedCamera, live_video_port: int | None, speaker: Speaker | None,
                   model: str | None) -> GeminiTriage:
    models = models_from_text(model)
    key = api_key_from_env()
    missing = None
    if not key:
        missing = "Set GEMINI_API_KEY and restart the dashboard to use Gemini."
    elif not live_video_port:
        missing = "Gemini needs the live camera with video (--camera-backend live)."
    if missing:
        print(f"Warning: {missing}", flush=True)
        return GeminiTriage(camera.status, fetch_snapshot, None, model=models[0], missing_reason=missing)
    snapshot_url = f"http://127.0.0.1:{live_video_port}/snapshot.jpg"
    return GeminiTriage(
        camera.status,
        lambda: fetch_snapshot(snapshot_url),
        lambda jpeg, context: call_gemini(jpeg, context, api_key=key, models=models),
        model=models[0],
        speaker=speaker,
    )


def create_app(
    service: RobotControlService | None = None,
    *,
    camera_backend: str = "mock",
    replay_path: str | Path | None = None,
    camera: MockCameraBackend | ReplayCameraBackend | LiveCameraBackend | None = None,
    camera_detector: str | Path = DEFAULT_DETECTOR,
    camera_args: str | None = None,
    video_port: int = DEFAULT_VIDEO_PORT,
    motor_backend: str = "mock",
    bridge_command_socket: str | Path | None = None,
    bridge_status_socket: str | Path | None = None,
    sensors_mode: str = "off",
    sensors: Sensors | None = None,
    voice: bool = False,
    camera_voice: CameraVoice | None = None,
    gemini: bool = False,
    gemini_triage: GeminiTriage | None = None,
    gemini_model: str | None = None,
    auto_accessories: bool = False,
    sim_command_socket: str | Path | None = None,
    autonomy_command_socket: str | Path | None = None,
    autonomy_status_socket: str | Path | None = None,
    allow_physical_autonomy: bool = False,
    autonomy_speed_percent: int = DEFAULT_AUTONOMY_SPEED_PERCENT,
    autonomy_min_pwm: int = DEFAULT_AUTONOMY_MIN_PWM,
) -> FastAPI:
    """Create the dashboard app with an injectable service for integration tests."""

    if service is not None:
        control_service = service
    elif motor_backend == "mock":
        control_service = RobotControlService()
    elif motor_backend == "bridge":
        run_dir = default_run_dir()
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Off unless --allow-physical-autonomy: the robot stays manual-only.
        autonomy_sockets: dict[str, Any] = {}
        if allow_physical_autonomy:
            # Its own folder, so the ROS container can mount only these
            # sockets and never reach the bridge's serial command socket.
            ros_dir = run_dir / "ros"
            ros_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            autonomy_sockets = {
                "allow_autonomy": True,
                "allow_physical_autonomy": True,
                "autonomy_speed_percent": autonomy_speed_percent,
                "autonomy_min_pwm": autonomy_min_pwm,
                "autonomy_endpoint": AutonomyHostEndpoint(
                    autonomy_command_socket or ros_dir / "autonomy-command.sock",
                    autonomy_status_socket or ros_dir / "autonomy-status.sock",
                ),
                "navigation_endpoint": NavigationHostEndpoint(
                    ros_dir / "navigation-goal.sock", ros_dir / "navigation-status.sock",
                ),
            }
        control_service = RobotControlService(
            backend=BridgeMotorBackend(
                bridge_command_socket or run_dir / "bridge-command.sock",
                bridge_status_socket or run_dir / "bridge-status.sock",
            ),
            **autonomy_sockets,
        )
    elif motor_backend == "gazebo":
        run_dir = default_sim_command_socket().parent
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        control_service = RobotControlService(
            backend=GazeboMotorBackend(sim_command_socket),
            allow_autonomy=True,
            autonomy_endpoint=AutonomyHostEndpoint(
                autonomy_command_socket or run_dir / "autonomy-command.sock",
                autonomy_status_socket or run_dir / "autonomy-status.sock",
            ),
            navigation_endpoint=NavigationHostEndpoint(
                run_dir / "navigation-goal.sock", run_dir / "navigation-status.sock",
            ),
        )
    else:
        raise ValueError("motor_backend must be mock, bridge, or gazebo")
    if allow_physical_autonomy and motor_backend != "bridge" and service is None:
        raise ValueError("allow_physical_autonomy needs motor_backend bridge")
    playback = SimulationPlayback() if isinstance(control_service.backend, GazeboMotorBackend) else None
    if camera is not None:
        camera_service = camera
    elif camera_backend == "mock":
        camera_service = MockCameraBackend()
    elif camera_backend == "replay":
        if replay_path is None:
            raise ValueError("replay_path is required when camera_backend is replay")
        camera_service = ReplayCameraBackend(replay_path)
    elif camera_backend == "live":
        camera_service = LiveCameraBackend(
            camera_detector,
            detector_args_from_env() if camera_args is None else camera_args,
            video_port=video_port,
        )
    else:
        raise ValueError("camera_backend must be mock, replay, or live")
    sensor_service = sensors if sensors is not None else create_sensors(sensors_mode)
    camera_status_source = _LockedCamera(camera_service)
    if auto_accessories and control_service.automation is None:
        control_service.automation = AccessoryAutomation(camera_status_source.status)
    automation = control_service.automation
    if camera_voice is None and voice:
        camera_voice = _create_camera_voice(camera_status_source)
    if gemini_triage is None and gemini:
        live_port = video_port if isinstance(camera_service, LiveCameraBackend) else None
        speaker = camera_voice.speaker if camera_voice is not None else None
        gemini_triage = _create_gemini(camera_status_source, live_port, speaker, gemini_model)
    if camera_voice is not None and gemini_triage is not None and gemini_triage.active:
        # Gemini is the robot's voice now; the fixed alerts are only its fallback.
        camera_voice.muted = True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        camera_service.start()
        sensor_service.start()
        if camera_voice is not None:
            camera_voice.start()
        if gemini_triage is not None:
            gemini_triage.start()
        if automation is not None:
            automation.start()
        app.state.control_loop = asyncio.create_task(_control_loop(control_service))
        if playback is not None:
            playback.start()
        try:
            yield
        finally:
            app.state.control_loop.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.control_loop
            control_service.stop("dashboard_shutdown")
            if playback is not None:
                await playback.close()
            control_service.close()
            if gemini_triage is not None:
                gemini_triage.close()
            if camera_voice is not None:
                camera_voice.close()
            if automation is not None:
                automation.close()
            camera_service.close()
            sensor_service.close()

    app = FastAPI(title="Rescuebot Dashboard", lifespan=lifespan)
    app.state.control_service = control_service
    app.state.camera_service = camera_service
    app.state.sensor_service = sensor_service
    app.state.camera_voice = camera_voice
    app.state.gemini = gemini_triage
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def state() -> dict[str, object]:
        return _dashboard_state(control_service, None, camera_status_source, sensor_service, playback, gemini_triage)

    @app.get("/api/lidar")
    async def lidar() -> dict[str, object]:
        return sensor_service.lidar()

    @app.get("/api/gemini/snapshot.jpg", include_in_schema=False)
    async def gemini_snapshot() -> Response:
        jpeg = gemini_triage.latest_jpeg() if gemini_triage is not None else None
        if jpeg is None:
            return Response(status_code=404)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/control")
    async def control_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        session = secrets.token_urlsafe(16)
        await websocket.send_json(
            {"type": "state", "data": _dashboard_state(control_service, session, camera_status_source, sensor_service, playback, gemini_triage)}
        )

        try:
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    control_service.stop("invalid_browser_message")
                    await websocket.send_json({"type": "error", "message": "Messages must be JSON objects."})
                    continue

                message_type = message.get("type")
                if message_type == "claim":
                    accepted = control_service.claim(session)
                    response: dict[str, Any] = {"type": "claim", "accepted": accepted}
                elif message_type == "enable":
                    accepted = not (playback is not None and playback.pending) and control_service.enable(session)
                    response = {"type": "enable", "accepted": accepted}
                elif message_type == "simulation_playback" and playback is not None:
                    accepted = (
                        session == control_service.control.owner_session
                        and not control_service.control.armed
                        and not control_service.autonomy.active
                        and not control_service.control.has_movement
                        and playback.request(message.get("rate"))
                    )
                    response = {"type": "simulation_playback", "accepted": accepted}
                elif message_type == "keys":
                    keys = message.get("keys")
                    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                        control_service.stop("invalid_browser_message")
                        response = {"type": "keys", "accepted": False}
                    else:
                        accepted = control_service.keys(session, keys)
                        response = {"type": "keys", "accepted": accepted}
                elif message_type == "speed":
                    delta = message.get("delta")
                    accepted = control_service.adjust_speed(session, delta)
                    response = {"type": "speed", "accepted": accepted}
                elif message_type == "start_autonomy":
                    mission = control_service.start_autonomy(session)
                    response = {"type": "start_autonomy", "accepted": mission is not None}
                elif message_type == "navigation_goal" and control_service.autonomy_available:
                    accepted = control_service.navigation_goal(session, message.get("forward"), message.get("right"))
                    response = {"type": "navigation_goal", "accepted": accepted}
                elif message_type == "start_search" and control_service.autonomy_available:
                    accepted = control_service.start_search(session)
                    response = {"type": "start_search", "accepted": accepted}
                elif message_type == "gemini_assess":
                    # Read-only camera analysis: never affects driving, so no owner check.
                    accepted = gemini_triage is not None and gemini_triage.request()
                    response = {"type": "gemini_assess", "accepted": accepted}
                elif message_type == "stop":
                    control_service.stop("operator_stop")
                    response = {"type": "stop", "accepted": True}
                elif message_type == "accessory":
                    name = message.get("name")
                    on = message.get("on")
                    if name not in ACCESSORY_NAMES or not isinstance(on, bool):
                        control_service.stop("invalid_browser_message")
                        response = {"type": "accessory", "accepted": False}
                    else:
                        accepted = control_service.set_accessory(session, name, on)
                        response = {"type": "accessory", "accepted": accepted}
                else:
                    control_service.stop("invalid_browser_message")
                    response = {"type": "error", "message": "Unsupported control message."}

                await websocket.send_json(response)
                await websocket.send_json(
                    {"type": "state", "data": _dashboard_state(control_service, session, camera_status_source, sensor_service, playback, gemini_triage)}
                )
        except WebSocketDisconnect:
            control_service.disconnect(session)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the Rescuebot dashboard.")
    parser.add_argument(
        "--camera-backend",
        choices=("mock", "replay", "live"),
        default=os.environ.get("RESCUEBOT_CAMERA_BACKEND", "mock"),
        help="live: run ai_camera_detect.py on the AI Camera in a separate process.",
    )
    parser.add_argument(
        "--autonomy-command-socket",
        default=os.environ.get("RESCUEBOT_AUTONOMY_COMMAND_SOCKET"),
        help="Simulation ROS adapter command socket (gazebo backend only).",
    )
    parser.add_argument(
        "--autonomy-status-socket",
        default=os.environ.get("RESCUEBOT_AUTONOMY_STATUS_SOCKET"),
        help="Simulation ROS adapter status socket (gazebo backend only).",
    )
    parser.add_argument(
        "--camera-args",
        default=detector_args_from_env(),
        help='Extra ai_camera_detect.py options for --camera-backend live (default: "%(default)s").',
    )
    parser.add_argument(
        "--video-port",
        type=int,
        default=int(os.environ.get("RESCUEBOT_VIDEO_PORT", DEFAULT_VIDEO_PORT)),
        help="Port for the live camera's MJPEG video; 0 turns video off (default: %(default)s).",
    )
    parser.add_argument(
        "--replay-path",
        default=os.environ.get("RESCUEBOT_REPLAY_PATH"),
        help="Detection JSONL recording used when --camera-backend replay.",
    )
    parser.add_argument(
        "--motor-backend",
        choices=("mock", "bridge", "gazebo"),
        default=os.environ.get("RESCUEBOT_MOTOR_BACKEND", "mock"),
        help="bridge: physical bridge; gazebo: simulation-only Unix-datagram backend.",
    )
    parser.add_argument(
        "--sim-command-socket",
        default=os.environ.get("RESCUEBOT_SIM_COMMAND_SOCKET"),
        help="Unix socket read by rescuebot_sim_bridge when --motor-backend gazebo is selected.",
    )
    parser.add_argument(
        "--sensors",
        choices=("off", "mock", "live"),
        default=os.environ.get("RESCUEBOT_SENSORS", "off"),
        help="live: show the LiDAR scan, read by its own process.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        default=os.environ.get("RESCUEBOT_VOICE") == "1",
        help="Speak camera detections (ElevenLabs, espeak-ng fallback) on its own thread.",
    )
    parser.add_argument(
        "--gemini",
        action="store_true",
        default=os.environ.get("RESCUEBOT_GEMINI") == "1",
        help="Ask Gemini whether each newly detected person needs help (needs GEMINI_API_KEY "
        "and --camera-backend live). Speaks its alert too when --voice is on.",
    )
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("RESCUEBOT_GEMINI_MODEL", ",".join(models_from_text(None))),
        help="Gemini model(s), comma-separated; later ones are tried when earlier ones are "
        "busy (default: %(default)s).",
    )
    parser.add_argument(
        "--auto-accessories",
        action="store_true",
        default=os.environ.get("RESCUEBOT_AUTO_ACCESSORIES") == "1",
        help="Sound the buzzer and flash the light when a person appears; light on in the dark.",
    )
    parser.add_argument(
        "--allow-physical-autonomy",
        action="store_true",
        default=os.environ.get("RESCUEBOT_ALLOW_PHYSICAL_AUTONOMY") == "1",
        help="Let the ROS 2 navigation container drive the real robot (bridge backend only). "
        "Manual keys and Stop still take over at once.",
    )
    parser.add_argument(
        "--autonomy-speed",
        type=int,
        default=int(os.environ.get("RESCUEBOT_AUTONOMY_SPEED", DEFAULT_AUTONOMY_SPEED_PERCENT)),
        help="Top autonomy speed as a percent of the PWM ceiling, 10-100 (default: %(default)s).",
    )
    parser.add_argument(
        "--autonomy-min-pwm",
        type=int,
        default=int(os.environ.get("RESCUEBOT_AUTONOMY_MIN_PWM", DEFAULT_AUTONOMY_MIN_PWM)),
        help="Real robot: scale weak autonomy commands up so the strongest wheel gets at least "
        "this PWM (motors stall below it); 0 disables (default: %(default)s).",
    )
    args = parser.parse_args()
    if args.allow_physical_autonomy and args.motor_backend != "bridge":
        parser.error("--allow-physical-autonomy needs --motor-backend bridge")
    if args.camera_backend == "replay" and args.replay_path is None:
        parser.error("--replay-path is required when --camera-backend replay")

    uvicorn.run(
        create_app(
            camera_backend=args.camera_backend,
            replay_path=args.replay_path,
            camera_args=args.camera_args,
            video_port=args.video_port,
            motor_backend=args.motor_backend,
            sensors_mode=args.sensors,
            voice=args.voice,
            gemini=args.gemini,
            gemini_model=args.gemini_model,
            auto_accessories=args.auto_accessories,
            sim_command_socket=args.sim_command_socket,
            autonomy_command_socket=args.autonomy_command_socket,
            autonomy_status_socket=args.autonomy_status_socket,
            allow_physical_autonomy=args.allow_physical_autonomy,
            autonomy_speed_percent=args.autonomy_speed,
            autonomy_min_pwm=args.autonomy_min_pwm,
        ),
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
