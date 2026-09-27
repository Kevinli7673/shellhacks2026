"""Automatic buzzer and light, driven by the camera.

AccessoryAutomation watches the dashboard's camera status on its own thread and
turns what it sees into two kinds of events:

- a person alert, when a person first appears (nothing more while they stay in
  view; a new alert needs `gone_after_s` seconds with nobody seen), and
- a dark/bright change, from the frame's lux estimate, with separate on/off
  thresholds and a hold time so the light doesn't flicker at the boundary.

It never writes the hardware itself. RobotControlService reads snapshot() on
its control tick, plays the alert (buzzer for ALERT_BUZZER_S, ALERT_FLASHES
light flashes), and treats a dark/bright change like a Light button press.
Camera polling stays on this thread, so a slow or failing camera can't delay
the control loop, and the buzzer and light never affect driving.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys
import threading
import time
from typing import Callable


ALERT_BUZZER_S = 2.0
ALERT_FLASHES = 5
ALERT_FLASH_PERIOD_S = 0.4  # 0.2 s on, 0.2 s off: five flashes take 2 s
DARK_LUX = 10.0  # darker than this for HOLD_S turns the light on
BRIGHT_LUX = 40.0  # brighter than this for HOLD_S turns it off again
HOLD_S = 5.0
PERSON_GONE_S = 3.0


@dataclass(frozen=True)
class AutomationSnapshot:
    alert_seq: int  # increases by one per person alert
    dark: bool
    dark_seq: int  # increases by one per dark/bright change
    lux: float | None

    def as_dict(self) -> dict[str, object]:
        return {"dark": self.dark, "lux": self.lux, "alerts": self.alert_seq}


def alert_outputs(elapsed_s: float) -> tuple[bool, bool | None] | None:
    """(buzzer, light) at `elapsed_s` into an alert; light None means "not flashing".

    Returns None once the alert is over.
    """
    flash_s = ALERT_FLASHES * ALERT_FLASH_PERIOD_S
    if elapsed_s < 0 or elapsed_s >= max(ALERT_BUZZER_S, flash_s):
        return None
    buzzer = elapsed_s < ALERT_BUZZER_S
    light = None
    if elapsed_s < flash_s:
        light = elapsed_s % ALERT_FLASH_PERIOD_S < ALERT_FLASH_PERIOD_S / 2
    return buzzer, light


def _stderr_log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class AccessoryAutomation:
    def __init__(
        self,
        camera_status: Callable[[], dict],
        *,
        interval_s: float = 0.25,
        dark_lux: float = DARK_LUX,
        bright_lux: float = BRIGHT_LUX,
        hold_s: float = HOLD_S,
        gone_after_s: float = PERSON_GONE_S,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
    ) -> None:
        if not dark_lux < bright_lux:
            raise ValueError("dark_lux must be below bright_lux")
        self.camera_status = camera_status
        self.interval_s = interval_s
        self.dark_lux = dark_lux
        self.bright_lux = bright_lux
        self.hold_s = hold_s
        self.gone_after_s = gone_after_s
        self.clock = clock
        self.log = log or _stderr_log
        self._lock = threading.Lock()
        self._present = False
        self._last_seen = float("-inf")
        self._alert_seq = 0
        self._dark = False
        self._dark_seq = 0
        self._lux: float | None = None
        self._pending_since: float | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rescuebot-accessory-auto", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> AutomationSnapshot:
        with self._lock:
            return AutomationSnapshot(self._alert_seq, self._dark, self._dark_seq, self._lux)

    def poll_once(self) -> None:
        now = self.clock()
        status = self.camera_status()
        online = status.get("status") == "online"
        detections = (status.get("detections") or []) if online else []
        person = any(d.get("label") == "person" for d in detections)
        lux = (status.get("frame") or {}).get("lux") if online else None
        with self._lock:
            self._update_person(person, now)
            self._update_dark(lux, now)

    def _update_person(self, person: bool, now: float) -> None:
        if not person:
            if self._present and now - self._last_seen >= self.gone_after_s:
                self._present = False
            return
        self._last_seen = now
        if not self._present:
            self._present = True
            self._alert_seq += 1
            self.log("[accessories] person detected: buzzer and light alert")

    def _update_dark(self, lux: object, now: float) -> None:
        if isinstance(lux, bool) or not isinstance(lux, (int, float)):
            # No reading (camera offline or stale): keep the current state.
            self._lux = None
            self._pending_since = None
            return
        self._lux = float(lux)
        wants_change = lux > self.bright_lux if self._dark else lux < self.dark_lux
        if not wants_change:
            self._pending_since = None
            return
        if self._pending_since is None:
            self._pending_since = now
        if now - self._pending_since >= self.hold_s:
            self._dark = not self._dark
            self._dark_seq += 1
            self._pending_since = None
            self.log(f"[accessories] {'dark' if self._dark else 'bright'} ({lux:.1f} lux): light "
                     f"{'on' if self._dark else 'off'}")

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.poll_once()
            except Exception as e:
                self.log(f"[accessories] camera poll failed ({type(e).__name__}: {e})")

    def close(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)
