"""Run one auxiliary sensor as an isolated child process and keep its newest record.

The webcam, microphone, and LiDAR readers each run as their own child process
(IMPLEMENTATION_PLAN.md section 2: sensor and camera work stays outside the
control-service and motor-bridge processes). The child prints one JSON object
per line on stdout. The dashboard keeps only the newest record of the expected
type and reports it as online, stale, or offline. A sensor failure only changes
that sensor's status; nothing here imports control, motor, or arming code.
"""

from __future__ import annotations

import json
import signal
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence


MAX_LINE_BYTES = 65536


class SensorProcess:
    """Start a sensor child process and track the freshness of its newest record."""

    def __init__(
        self,
        name: str,
        command: Sequence[str],
        record_type: str,
        *,
        expiry_s: float = 2.0,
        starting_message: str = "Starting.",
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self._command = list(command)
        self.record_type = record_type
        self.expiry_s = expiry_s
        self.starting_message = starting_message
        self._env = None if env is None else dict(env)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._start_error: str | None = None
        self._latest: dict[str, Any] | None = None
        self._latest_at: float | None = None
        self._last_message: str | None = None
        self._records = 0
        self._invalid_lines = 0

    def command(self) -> list[str]:
        return list(self._command)

    def start(self) -> None:
        if self._process is not None or self._start_error is not None:
            return
        try:
            # Own session, so a Ctrl+C in the dashboard terminal reaches only the
            # dashboard, which then stops this child from close().
            self._process = subprocess.Popen(
                self._command,
                stdout=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                start_new_session=True,
                env=self._env,
            )
        except OSError as exc:
            self._start_error = f"{self.name} process could not start: {exc}"
            return
        self._reader = threading.Thread(
            target=self._read, args=(self._process,), name=f"{self.name}-reader", daemon=True
        )
        self._reader.start()

    def _read(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        with process.stdout:
            self._read_lines(process.stdout)

    def _read_lines(self, lines: Any) -> None:
        for line in lines:
            if not line.strip():
                continue
            try:
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError("line too long")
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record must be an object")
            except ValueError:  # json.JSONDecodeError is a ValueError
                with self._lock:
                    self._invalid_lines += 1
                continue
            kind = record.get("type")
            with self._lock:
                if kind == self.record_type:
                    self._latest = record
                    self._latest_at = time.monotonic()
                    self._records += 1
                elif kind == "sensor_message" and isinstance(record.get("message"), str):
                    # A child explains why it has no data (device missing, and so on).
                    self._last_message = record["message"][:200]
                else:
                    self._invalid_lines += 1

    def close(self, timeout_s: float = 3.0) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.send_signal(signal.SIGINT)  # lets the child release its device cleanly
                process.wait(timeout_s)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout_s)
            except ProcessLookupError:
                pass
        if self._reader is not None:
            self._reader.join(timeout_s)  # the reader closes stdout at end of file

    def snapshot(self, now: float | None = None) -> tuple[str, str, dict[str, Any] | None, int | None]:
        """Return (status, message, newest record or None, age in ms or None)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            latest = self._latest
            latest_at = self._latest_at
            last_message = self._last_message
        process = self._process
        alive = process is not None and process.poll() is None
        age_s = None if latest_at is None else max(0.0, now - latest_at)

        if age_s is not None and age_s <= self.expiry_s and alive:
            return "online", "Live.", latest, round(age_s * 1000)
        if self._start_error is not None:
            return "offline", self._start_error, None, None
        if process is not None and not alive:
            reason = last_message or f"{self.name} process stopped (exit code {process.returncode})."
            return "offline", reason, None, None
        if age_s is not None:
            return "stale", last_message or f"{self.name} data is stale.", latest, round(age_s * 1000)
        if alive:
            return "offline", last_message or self.starting_message, None, None
        return "offline", f"{self.name} is not running.", None, None

    def counters(self) -> dict[str, int]:
        with self._lock:
            return {"records": self._records, "invalid_lines": self._invalid_lines}
