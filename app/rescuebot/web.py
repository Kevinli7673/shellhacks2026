"""FastAPI dashboard transport for the manual-control service."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
import asyncio
from pathlib import Path
import secrets
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .service import RobotControlService


STATIC_DIR = Path(__file__).with_name("static")
CONTROL_TICK_SECONDS = 0.05


async def _control_loop(service: RobotControlService) -> None:
    while True:
        service.tick()
        await asyncio.sleep(CONTROL_TICK_SECONDS)


def _dashboard_state(service: RobotControlService, session: str) -> dict[str, object]:
    state = service.state()
    control = state["control"]
    assert isinstance(control, dict)
    return {
        "session": session,
        "can_control": control["owner_session"] in (None, session),
        **state,
    }


def create_app(service: RobotControlService | None = None) -> FastAPI:
    """Create the dashboard app with an injectable service for integration tests."""

    control_service = service or RobotControlService()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.control_loop = asyncio.create_task(_control_loop(control_service))
        try:
            yield
        finally:
            app.state.control_loop.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.control_loop

    app = FastAPI(title="Rescuebot Dashboard", lifespan=lifespan)
    app.state.control_service = control_service
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def state() -> dict[str, object]:
        return control_service.state()

    @app.websocket("/ws/control")
    async def control_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        session = secrets.token_urlsafe(16)
        await websocket.send_json({"type": "state", "data": _dashboard_state(control_service, session)})

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
                await websocket.send_json({"type": "state", "data": _dashboard_state(control_service, session)})
        except WebSocketDisconnect:
            control_service.disconnect(session)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("rescuebot.web:app", host="0.0.0.0", port=8000, reload=False)
