"""Application control service independent of FastAPI transport details."""

from __future__ import annotations

from dataclasses import replace
import time

from .autonomy import AUTONOMY_TIMEOUT_S, DEFAULT_AUTONOMY_SPEED_PERCENT, AutonomyControl
from .autonomy_ipc import AutonomyHostEndpoint
from .bridge_backend import BridgeMotorBackend
from .control import ControlSnapshot, ManualControl
from .gazebo_backend import GazeboMotorBackend
from .mecanum import mix_mecanum
from .mock import MockMotorBackend
from .navigation_ipc import NavigationHostEndpoint


ARM_CONFIRM_TIMEOUT_S = 1.0


class RobotControlService:
    """Connect browser commands to a motor backend at a fixed cadence.

    With the mock backend, Enable Driving arms immediately. With the bridge
    backend, Enable sends an arm request and driving stays at zero ("arming")
    until the firmware's arm_ack arrives; no confirmation within
    ARM_CONFIRM_TIMEOUT_S stops with "arm_timeout". Any later loss of firmware
    arming stops control with the bridge's reason and needs a new Enable.
    """

    def __init__(
        self,
        control: ManualControl | None = None,
        backend: MockMotorBackend | BridgeMotorBackend | GazeboMotorBackend | None = None,
        arm_confirm_timeout_s: float = ARM_CONFIRM_TIMEOUT_S,
        *,
        allow_autonomy: bool = False,
        autonomy_endpoint: AutonomyHostEndpoint | None = None,
        navigation_endpoint: NavigationHostEndpoint | None = None,
        autonomy_timeout_s: float = AUTONOMY_TIMEOUT_S,
        autonomy_speed_percent: int = DEFAULT_AUTONOMY_SPEED_PERCENT,
    ) -> None:
        self.control = control or ManualControl()
        self.backend = backend or MockMotorBackend()
        self.arm_confirm_timeout_s = arm_confirm_timeout_s
        self.allow_autonomy = allow_autonomy
        self.autonomy = AutonomyControl(autonomy_timeout_s)
        self.autonomy_endpoint = autonomy_endpoint
        self.navigation_endpoint = navigation_endpoint
        self.autonomy_speed_percent = max(10, min(100, int(autonomy_speed_percent)))
        self._arming_since: float | None = None
        self._last_snapshot: ControlSnapshot | None = None

    @property
    def _bridge(self) -> BridgeMotorBackend | None:
        return self.backend if isinstance(self.backend, BridgeMotorBackend) else None

    @property
    def arming(self) -> bool:
        return self._arming_since is not None

    def tick(self, now: float | None = None) -> ControlSnapshot:
        now = time.monotonic() if now is None else now
        self._poll_autonomy(now)
        snapshot = self.control.snapshot(now)
        snapshot = self._select_source(snapshot, now)
        bridge = self._bridge
        if bridge is None:
            reason = snapshot.fault or (snapshot.source if snapshot.armed else "disarmed")
            self.backend.apply_snapshot(snapshot, reason, now)
        else:
            snapshot = self._reconcile_bridge(bridge, snapshot, now)
            bridge.send(snapshot, now)
        self._last_snapshot = snapshot
        self._publish_autonomy_status()
        return snapshot

    def _poll_autonomy(self, now: float) -> None:
        endpoint = self.autonomy_endpoint
        if endpoint is None:
            return
        for intent in endpoint.drain():
            self.autonomy.receive(intent)

    def _publish_autonomy_status(self) -> None:
        if self.autonomy_endpoint is not None:
            self.autonomy_endpoint.publish(self.autonomy.status())

    def _select_source(self, snapshot: ControlSnapshot, now: float) -> ControlSnapshot:
        """Apply the fixed manual-over-autonomy priority before the backend.

        The simulation branch intentionally permits autonomy only when the
        explicitly selected Gazebo backend is active.  A physical bridge keeps
        the original manual-only behavior until a separately gated host branch
        is created from physical acceptance.
        """
        if not self.autonomy.active or not self.allow_autonomy or not isinstance(self.backend, GazeboMotorBackend):
            return snapshot
        if not snapshot.armed:
            self.autonomy.cancel(snapshot.fault or "motor_disarmed")
            return snapshot
        if self.control.has_movement:
            self.autonomy.cancel("manual_override")
            return snapshot
        if self.navigation_endpoint is not None:
            navigation = self.navigation_endpoint.state(now)
            phase = navigation.get("search", {}).get("phase")
            if navigation.get("mission") == self.autonomy.mission and phase in {"complete", "failed"}:
                reason = "search_complete" if phase == "complete" else "search_failed"
                self.control.stop(reason)
                self.autonomy.cancel(reason)
                return self.control.snapshot(now)
        motion = self.autonomy.motion(now)
        if motion is None:
            self.control.stop("autonomy_timeout")
            return self.control.snapshot(now)
        speed_limit = round(self.control.pwm_ceiling * self.autonomy_speed_percent / 100)
        return replace(
            snapshot,
            speed_percent=self.autonomy_speed_percent,
            speed_limit=speed_limit,
            intent=motion,
            wheels=mix_mecanum(motion.forward, motion.sideways, motion.turn, speed_limit),
            source="autonomy",
        )

    def _reconcile_bridge(
        self, bridge: BridgeMotorBackend, snapshot: ControlSnapshot, now: float
    ) -> ControlSnapshot:
        bridge.poll(now)
        if not snapshot.armed:
            self._arming_since = None
            return snapshot
        if self._arming_since is not None:
            if bridge.firmware_armed:
                self._arming_since = None
                bridge.arming = False
            elif now - self._arming_since > self.arm_confirm_timeout_s:
                self._stop_from_backend("arm_timeout")
                return self.control.snapshot(now)
            return snapshot
        if not bridge.firmware_armed or not bridge.healthy(now):
            # Firmware reboot, watchdog, lost link, or a dead bridge process:
            # never keep the operator "enabled" on a disarmed robot.
            reason = bridge.fault if bridge.healthy(now) else "backend_unavailable"
            self._stop_from_backend(reason or "motor_disarmed")
            return self.control.snapshot(now)
        return snapshot

    def _stop_from_backend(self, reason: str) -> None:
        self._arming_since = None
        self.control.stop(reason)

    def claim(self, session: str) -> bool:
        return self.control.claim(session)

    def enable(self, session: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        bridge = self._bridge
        if bridge is not None:
            bridge.poll(now)
            healthy = bridge.healthy(now)
        else:
            healthy = self.backend.healthy
        was_armed = self.control.armed
        accepted = self.control.enable(session, now, backend_healthy=healthy)
        if accepted and bridge is not None and not (was_armed and bridge.firmware_armed):
            self._arming_since = now
            bridge.request_arm(now)
        self.tick(now)
        return accepted

    def keys(self, session: str, keys: list[str], now: float | None = None) -> bool:
        accepted = self.control.set_keys(session, keys, now)
        if accepted and keys:
            self.autonomy.cancel("manual_override")
        self.tick(time.monotonic() if now is None else now)
        return accepted

    def adjust_speed(self, session: str, delta_percent: int) -> bool:
        accepted = self.control.adjust_speed(session, delta_percent)
        self.tick()
        return accepted

    def stop(self, reason: str = "operator_stop", now: float | None = None) -> None:
        self._arming_since = None
        self.autonomy.cancel(reason)
        self.control.stop(reason)
        self.tick(now)

    def disconnect(self, session: str, now: float | None = None) -> None:
        self.autonomy.cancel("browser_disconnected")
        self.control.disconnect(session)
        self.tick(now)

    def start_autonomy(self, session: str) -> str | None:
        """Explicitly arm the simulation autonomy source for its next ROS intent."""
        if (
            not self.allow_autonomy
            or self._bridge is not None
            or not isinstance(self.backend, GazeboMotorBackend)
            or session != self.control.owner_session
            or not self.control.armed
            or self.control.has_movement
            or self.autonomy.active
            or (self.navigation_endpoint is not None and not self.navigation_endpoint.state()["ready"])
        ):
            return None
        mission = self.autonomy.start(now=time.monotonic())
        self._publish_autonomy_status()
        return mission

    def navigation_goal(self, session: str, forward: object, right: object) -> bool:
        """A destination may only enter the current, explicitly started sim mission."""
        self.tick()
        if (not self.allow_autonomy or not isinstance(self.backend, GazeboMotorBackend)
                or self.navigation_endpoint is None or session != self.control.owner_session
                or not self.control.armed or self.control.has_movement
                or not self.autonomy.active or self.autonomy.mission is None):
            return False
        try:
            return self.navigation_endpoint.send_goal(self.autonomy.mission, forward, right)
        except ValueError:
            return False

    def start_search(self, session: str) -> bool:
        """Start the simulation search only within an explicitly enabled mission."""
        self.tick()
        if (not self.allow_autonomy or not isinstance(self.backend, GazeboMotorBackend)
                or self.navigation_endpoint is None or session != self.control.owner_session
                or not self.control.armed or self.control.has_movement
                or not self.autonomy.active or self.autonomy.mission is None):
            return False
        return self.navigation_endpoint.start_search(self.autonomy.mission)

    def close(self) -> None:
        bridge = self._bridge
        if bridge is not None:
            bridge.close()
        elif isinstance(self.backend, GazeboMotorBackend):
            self.backend.close()
        if self.autonomy_endpoint is not None:
            self.autonomy_endpoint.close()
        if self.navigation_endpoint is not None:
            self.navigation_endpoint.close()

    def state(self, now: float | None = None) -> dict[str, object]:
        now = time.monotonic() if now is None else now
        snapshot = self.tick(now)
        bridge = self._bridge
        motor = bridge.as_dict(now) if bridge is not None else self.backend.as_dict()
        control = snapshot.as_dict()
        control["arming"] = self.arming
        autonomy = self.autonomy.status()
        state = {
            "control": control,
            "autonomy": {
                "available": self.allow_autonomy and isinstance(self.backend, GazeboMotorBackend),
                "active": autonomy.active,
                "mission": autonomy.mission,
                "reason": autonomy.reason,
                "rejected_commands": None if self.autonomy_endpoint is None else self.autonomy_endpoint.rejected_commands,
            },
            "motor": motor,
            "camera": {
                "backend": "mock",
                "status": "offline",
                "message": "Camera service has not been integrated.",
            },
        }
        if self.allow_autonomy and isinstance(self.backend, GazeboMotorBackend) and self.navigation_endpoint is not None:
            state["autonomy"]["navigation"] = self.navigation_endpoint.state(now)
        return state
