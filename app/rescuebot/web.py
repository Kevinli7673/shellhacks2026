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
from .autonomy_ipc import AutonomyHostEndpoint
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
) -> dict[str, object]:
    state = service.state()
    state["camera"] = camera.status()
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
    sim_command_socket: str | Path | None = None,
    autonomy_command_socket: str | Path | None = None,
    autonomy_status_socket: str | Path | None = None,
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        camera_service.start()
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

    app = FastAPI(title="Rescuebot Dashboard", lifespan=lifespan)
    app.state.control_service = control_service
    app.state.camera_service = camera_service
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def state() -> dict[str, object]:
        return _dashboard_state(control_service, None, camera_service)

    @app.websocket("/ws/control")
    async def control_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        session = secrets.token_urlsafe(16)
        await websocket.send_json(
            {"type": "state", "data": _dashboard_state(control_service, session, camera_service)}
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
                elif message_type == "start_autonomy":
                    mission = control_service.start_autonomy(session)
                    response = {"type": "start_autonomy", "accepted": mission is not None}
                elif message_type == "navigation_goal" and isinstance(control_service.backend, GazeboMotorBackend):
                    accepted = control_service.navigation_goal(session, message.get("forward"), message.get("right"))
                    response = {"type": "navigation_goal", "accepted": accepted}
                elif message_type == "stop":
                    control_service.stop("operator_stop")
                    response = {"type": "stop", "accepted": True}
                else:
                    control_service.stop("invalid_browser_message")
                    response = {"type": "error", "message": "Unsupported control message."}

                await websocket.send_json(response)
                await websocket.send_json(
                    {"type": "state", "data": _dashboard_state(control_service, session, camera_service)}
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
            sim_command_socket=args.sim_command_socket,
            autonomy_command_socket=args.autonomy_command_socket,
            autonomy_status_socket=args.autonomy_status_socket,
        ),
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
