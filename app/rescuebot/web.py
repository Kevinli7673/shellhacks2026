"""FastAPI dashboard transport for the manual-control service."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
import asyncio
import argparse
import os
from pathlib import Path
import secrets
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .bridge_backend import BridgeMotorBackend
from .live_camera import (
    DEFAULT_DETECTOR,
    DEFAULT_VIDEO_PORT,
    LiveCameraBackend,
    detector_args_from_env,
)
from .motor_bridge import default_run_dir
from .replay_camera import MockCameraBackend, ReplayCameraBackend
from .sensors import DEFAULT_WEBCAM_PORT, OffSensors, Sensors, create_sensors
from .service import RobotControlService


STATIC_DIR = Path(__file__).with_name("static")
CONTROL_TICK_SECONDS = 0.05


async def _control_loop(service: RobotControlService) -> None:
    while True:
        service.tick()
        await asyncio.sleep(CONTROL_TICK_SECONDS)


def _dashboard_state(
    service: RobotControlService,
    session: str | None,
    camera: MockCameraBackend | ReplayCameraBackend | LiveCameraBackend,
    sensors: Sensors | None = None,
) -> dict[str, object]:
    state = service.state()
    state["camera"] = camera.status()
    state["sensors"] = (sensors or OffSensors()).status()
    control = state["control"]
    assert isinstance(control, dict)
    return {
        "session": session,
        "can_control": control["owner_session"] in (None, session),
        **state,
    }


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
    webcam_port: int = DEFAULT_WEBCAM_PORT,
) -> FastAPI:
    """Create the dashboard app with an injectable service for integration tests."""

    if service is not None:
        control_service = service
    elif motor_backend == "mock":
        control_service = RobotControlService()
    elif motor_backend == "bridge":
        run_dir = default_run_dir()
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        control_service = RobotControlService(
            backend=BridgeMotorBackend(
                bridge_command_socket or run_dir / "bridge-command.sock",
                bridge_status_socket or run_dir / "bridge-status.sock",
            )
        )
    else:
        raise ValueError("motor_backend must be mock or bridge")
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
    sensor_service = sensors if sensors is not None else create_sensors(sensors_mode, webcam_port=webcam_port)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        camera_service.start()
        sensor_service.start()
        app.state.control_loop = asyncio.create_task(_control_loop(control_service))
        try:
            yield
        finally:
            app.state.control_loop.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.control_loop
            control_service.stop("dashboard_shutdown")
            control_service.close()
            camera_service.close()
            sensor_service.close()

    app = FastAPI(title="Rescuebot Dashboard", lifespan=lifespan)
    app.state.control_service = control_service
    app.state.camera_service = camera_service
    app.state.sensor_service = sensor_service
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def state() -> dict[str, object]:
        return _dashboard_state(control_service, None, camera_service, sensor_service)

    @app.get("/api/lidar")
    async def lidar() -> dict[str, object]:
        return sensor_service.lidar()

    @app.websocket("/ws/control")
    async def control_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        session = secrets.token_urlsafe(16)
        await websocket.send_json(
            {"type": "state", "data": _dashboard_state(control_service, session, camera_service, sensor_service)}
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
                    accepted = control_service.enable(session)
                    response = {"type": "enable", "accepted": accepted}
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
                elif message_type == "stop":
                    control_service.stop("operator_stop")
                    response = {"type": "stop", "accepted": True}
                else:
                    control_service.stop("invalid_browser_message")
                    response = {"type": "error", "message": "Unsupported control message."}

                await websocket.send_json(response)
                await websocket.send_json(
                    {"type": "state", "data": _dashboard_state(control_service, session, camera_service, sensor_service)}
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
        choices=("mock", "bridge"),
        default=os.environ.get("RESCUEBOT_MOTOR_BACKEND", "mock"),
        help="bridge: send commands to a separately started rescuebot.motor_bridge process.",
    )
    parser.add_argument(
        "--sensors",
        choices=("off", "mock", "live"),
        default=os.environ.get("RESCUEBOT_SENSORS", "off"),
        help="live: show the USB webcam, its microphone level, and the LiDAR, each read by its own process.",
    )
    parser.add_argument(
        "--webcam-port",
        type=int,
        default=int(os.environ.get("RESCUEBOT_WEBCAM_PORT", DEFAULT_WEBCAM_PORT)),
        help="Port for the USB webcam's MJPEG video with --sensors live (default: %(default)s).",
    )
    args = parser.parse_args()
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
            webcam_port=args.webcam_port,
        ),
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
