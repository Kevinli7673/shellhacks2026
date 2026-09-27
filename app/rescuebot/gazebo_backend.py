"""Simulation-only motor backend which sends selected host intents to Gazebo."""

from __future__ import annotations

import os
import math
from pathlib import Path
import time

from .bridge_ipc import DatagramSender
from .control import ControlSnapshot
from .mecanum import WheelOutputs
from .simulation_ipc import SimulationCommand


COMMAND_TTL_S = 0.250


def default_sim_command_socket() -> Path:
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/rescuebot-{os.getuid()}"))
    return runtime_dir / "gazebo-command.sock"


class GazeboMotorBackend:
    """A local-only backend for a Gazebo command bridge.

    The backend mirrors the host's selected command at the normal control
    cadence.  A missing Gazebo receiver drops commands rather than blocking
    dashboard safety.  It is never a valid physical motor backend.
    """

    def __init__(self, command_socket: str | Path | None = None) -> None:
        self.command_socket = Path(command_socket or default_sim_command_socket())
        self.command_socket.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._sender = DatagramSender(self.command_socket)
        self._seq = 0
        self.healthy = True
        self.wheels = WheelOutputs.stopped()
        self.last_reason = "boot"

    def apply_snapshot(self, snapshot: ControlSnapshot, reason: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._seq += 1
        armed = snapshot.armed and reason in {"manual", "autonomy"}
        speed_scale = snapshot.speed_percent / 100.0
        translation_scale = max(1.0, math.hypot(snapshot.intent.forward, snapshot.intent.sideways))
        command = SimulationCommand(
            seq=self._seq,
            expires_at=now + COMMAND_TTL_S,
            armed=armed,
            forward=snapshot.intent.forward / translation_scale * speed_scale if armed else 0.0,
            sideways=snapshot.intent.sideways / translation_scale * speed_scale if armed else 0.0,
            turn=snapshot.intent.turn * speed_scale if armed else 0.0,
        )
        self._sender.send(command.encode())
        self.wheels = snapshot.wheels if armed else WheelOutputs.stopped()
        self.last_reason = reason

    def close(self) -> None:
        self._seq += 1
        self._sender.send(
            SimulationCommand(self._seq, time.monotonic() + COMMAND_TTL_S, False).encode()
        )
        self._sender.close()

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": "gazebo",
            "healthy": self.healthy,
            "wheels": self.wheels.as_dict(),
            "last_reason": self.last_reason,
            "command_socket": str(self.command_socket),
            "dropped_commands": self._sender.dropped_count,
        }
