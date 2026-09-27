"""Application control service independent of FastAPI transport details."""

from __future__ import annotations

from dataclasses import replace
import time

from .accessory_auto import AccessoryAutomation, alert_outputs
from .autonomy import AUTONOMY_TIMEOUT_S, DEFAULT_AUTONOMY_SPEED_PERCENT, AutonomyControl
from .autonomy_ipc import AutonomyHostEndpoint
from .bridge_backend import BridgeMotorBackend
from .control import ControlSnapshot, ManualControl
from .gazebo_backend import GazeboMotorBackend
from .mecanum import mix_mecanum
from .mock import MockMotorBackend
from .navigation_ipc import NavigationHostEndpoint


ARM_CONFIRM_TIMEOUT_S = 1.0
ACCESSORY_NAMES = ("buzzer", "light")
# With nothing sent for this long and no alert playing, a firmware state that
# differs from ours (reboot, new serial session, lost command) is adopted.
ACCESSORY_ADOPT_S = 1.0


class RobotControlService:
    """Connect browser commands to a motor backend at a fixed cadence.

    With the mock backend, Enable Driving arms immediately. With the bridge
    backend, Enable sends an arm request and driving stays at zero ("arming")
    until the firmware's arm_ack arrives; no confirmation within
    ARM_CONFIRM_TIMEOUT_S stops with "arm_timeout". Any later loss of firmware
    arming stops control with the bridge's reason and needs a new Enable.

    The buzzer and light have a base state set by the owner's buttons. With an
    AccessoryAutomation, a dark/bright change sets the base light like a button
    press (so a press overrides it until the next change), and a person alert
    plays on top of the base: buzzer for 2 s, five light flashes. Either never
    touches driving or arming.
    """

    def __init__(
        self,
        control: ManualControl | None = None,
        backend: MockMotorBackend | BridgeMotorBackend | GazeboMotorBackend | None = None,
        arm_confirm_timeout_s: float = ARM_CONFIRM_TIMEOUT_S,
        automation: AccessoryAutomation | None = None,
        *,
        allow_autonomy: bool = False,
        allow_physical_autonomy: bool = False,
        autonomy_endpoint: AutonomyHostEndpoint | None = None,
        navigation_endpoint: NavigationHostEndpoint | None = None,
        autonomy_timeout_s: float = AUTONOMY_TIMEOUT_S,
        autonomy_speed_percent: int = DEFAULT_AUTONOMY_SPEED_PERCENT,
    ) -> None:
        self.control = control or ManualControl()
        self.backend = backend or MockMotorBackend()
        self.arm_confirm_timeout_s = arm_confirm_timeout_s
        self.automation = automation
        self.allow_autonomy = allow_autonomy
        self.allow_physical_autonomy = allow_physical_autonomy
        self.autonomy = AutonomyControl(autonomy_timeout_s)
        self.autonomy_endpoint = autonomy_endpoint
        self.navigation_endpoint = navigation_endpoint
        self.autonomy_speed_percent = max(10, min(100, int(autonomy_speed_percent)))
        self._arming_since: float | None = None
        self._last_snapshot: ControlSnapshot | None = None
        self._base = {"buzzer": False, "light": False}
        self._sent = {"buzzer": False, "light": False}
        self._sent_at = float("-inf")
        self._alert_started: float | None = None
        self._seen_alert_seq = 0
        self._seen_dark_seq = 0

    @property
    def _bridge(self) -> BridgeMotorBackend | None:
        return self.backend if isinstance(self.backend, BridgeMotorBackend) else None

    @property
    def autonomy_available(self) -> bool:
        """Autonomy may drive Gazebo, or the physical bridge only when explicitly allowed.

        The physical path keeps every manual safeguard: Stop and any held key
        cancel autonomy, firmware disarm or a stale bridge cancels it, and a
        silent ROS source expires after AUTONOMY_TIMEOUT_S.
        """
        if not self.allow_autonomy:
            return False
        if isinstance(self.backend, GazeboMotorBackend):
            return True
        return self.allow_physical_autonomy and self._bridge is not None

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
        self._sync_accessories(now)
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
        if not self.autonomy.active or not self.autonomy_available:
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

    def set_accessory(self, session: str, name: str, on: bool, now: float | None = None) -> bool:
        """Owner-only buzzer/light switch. Works armed or disarmed; never moves the robot.

        Switching the buzzer off also ends a person alert that is playing.
        """
        now = time.monotonic() if now is None else now
        if name not in ACCESSORY_NAMES or session != self.control.owner_session:
            return False
        bridge = self._bridge
        if bridge is not None:
            bridge.poll(now)
            if not bridge.healthy(now):
                return False
        self._base[name] = on
        if name == "buzzer" and not on:
            self._alert_started = None
        return self._sync_accessories(now, force=True)

    def _follow_automation(self, now: float) -> None:
        if self.automation is None:
            return
        snap = self.automation.snapshot()
        if snap.alert_seq != self._seen_alert_seq:
            self._seen_alert_seq = snap.alert_seq
            self._alert_started = now
        if snap.dark_seq != self._seen_dark_seq:
            self._seen_dark_seq = snap.dark_seq
            self._base["light"] = snap.dark

    def _wanted_accessories(self, now: float) -> dict[str, bool]:
        wanted = dict(self._base)
        if self._alert_started is not None:
            outputs = alert_outputs(now - self._alert_started)
            if outputs is None:
                self._alert_started = None
            else:
                buzzer, light = outputs
                wanted["buzzer"] = wanted["buzzer"] or buzzer
                if light is not None:
                    wanted["light"] = light
        return wanted

    def _sync_accessories(self, now: float, force: bool = False) -> bool:
        """Send the wanted buzzer/light state when it changes (or when forced)."""
        self._follow_automation(now)
        wanted = self._wanted_accessories(now)
        bridge = self._bridge
        if isinstance(self.backend, GazeboMotorBackend):
            return False  # the simulated robot has no buzzer or light
        if bridge is None:
            if force or wanted != self.backend.accessories:
                self.backend.set_accessories(wanted["buzzer"], wanted["light"])
            return True
        if not bridge.healthy(now):
            return False
        if force or wanted != self._sent:
            self._sent = wanted
            self._sent_at = now
            return bridge.request_accessories(wanted["buzzer"], wanted["light"], now)
        if self._alert_started is None and now - self._sent_at >= ACCESSORY_ADOPT_S:
            confirmed = bridge.accessories
            if confirmed != wanted:
                # The firmware rebooted, a new serial session switched them
                # off, or a command was lost: follow what the firmware shows.
                self._base = dict(confirmed)
                self._sent = dict(confirmed)
                self._sent_at = now
        return True

    def _accessories_off(self, now: float) -> None:
        # The owner's switches reset; an automatic dark-scene light stays on.
        dark = self.automation is not None and self.automation.snapshot().dark
        self._base = {"buzzer": False, "light": dark}
        self._sync_accessories(now, force=True)

    def disconnect(self, session: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if session == self.control.owner_session:
            self._accessories_off(now)
        self.autonomy.cancel("browser_disconnected")
        self.control.disconnect(session)
        self.tick(now)

    def start_autonomy(self, session: str) -> str | None:
        """Explicitly arm the autonomy source for its next ROS intent."""
        if (
            not self.autonomy_available
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
            bridge.request_accessories(False, False, time.monotonic())
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
        state: dict[str, object] = {
            "control": control,
            "autonomy": {
                "available": self.autonomy_available,
                "physical": self.autonomy_available and self._bridge is not None,
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
        if self.automation is not None:
            state["accessory_auto"] = {
                **self.automation.snapshot().as_dict(),
                "alert_playing": self._alert_started is not None,
            }
        if self.autonomy_available and self.navigation_endpoint is not None:
            state["autonomy"]["navigation"] = self.navigation_endpoint.state(now)
        return state
