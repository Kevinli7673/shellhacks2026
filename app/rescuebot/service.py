"""Application control service independent of FastAPI transport details."""

from __future__ import annotations

import time

from .bridge_backend import BridgeMotorBackend
from .control import ControlSnapshot, ManualControl
from .mock import MockMotorBackend


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
        backend: MockMotorBackend | BridgeMotorBackend | None = None,
        arm_confirm_timeout_s: float = ARM_CONFIRM_TIMEOUT_S,
    ) -> None:
        self.control = control or ManualControl()
        self.backend = backend or MockMotorBackend()
        self.arm_confirm_timeout_s = arm_confirm_timeout_s
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
        snapshot = self.control.snapshot(now)
        bridge = self._bridge
        if bridge is None:
            reason = snapshot.fault or ("manual" if snapshot.armed else "disarmed")
            self.backend.apply(snapshot.wheels, reason)
        else:
            snapshot = self._reconcile_bridge(bridge, snapshot, now)
            bridge.send(snapshot, now)
        self._last_snapshot = snapshot
        return snapshot

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
        self.tick(time.monotonic() if now is None else now)
        return accepted

    def adjust_speed(self, session: str, delta_percent: int) -> bool:
        accepted = self.control.adjust_speed(session, delta_percent)
        self.tick()
        return accepted

    def stop(self, reason: str = "operator_stop", now: float | None = None) -> None:
        self._arming_since = None
        self.control.stop(reason)
        self.tick(now)

    def disconnect(self, session: str, now: float | None = None) -> None:
        self.control.disconnect(session)
        self.tick(now)

    def close(self) -> None:
        bridge = self._bridge
        if bridge is not None:
            bridge.close()

    def state(self, now: float | None = None) -> dict[str, object]:
        now = time.monotonic() if now is None else now
        snapshot = self.tick(now)
        bridge = self._bridge
        motor = bridge.as_dict(now) if bridge is not None else self.backend.as_dict()
        control = snapshot.as_dict()
        control["arming"] = self.arming
        return {
            "control": control,
            "motor": motor,
            "camera": {
                "backend": "mock",
                "status": "offline",
                "message": "Camera service has not been integrated.",
            },
        }
