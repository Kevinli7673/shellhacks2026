"""Bounded nonblocking pyserial transport for the ESP32-S2 motor controller.

The bridge accepts only a stable ``/dev/serial/by-id/...`` path.  It does not
guess from ``ttyUSB*`` or ``ttyACM*`` names: the RPLIDAR's CP210x adapter can
also appear as a USB serial port, and opening the wrong device would make the
motor link unreliable.

This module deliberately owns no arming or command retry policy.  A partial
write or serial error closes the port and raises ``SerialTransportError`` so
``MotorBridge`` can disarm locally, drop the current movement, and establish a
fresh session on a later reconnect attempt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from .serial_protocol import BAUD_RATE


SERIAL_BY_ID_DIRECTORY = Path("/dev/serial/by-id")
DEFAULT_MAX_READ_BYTES = 512


class SerialTransportError(OSError):
    """An unavailable, closed, or unsafe serial link."""


class SerialPort(Protocol):
    """The bounded subset of pyserial used by the motor bridge."""

    is_open: bool
    in_waiting: int

    def write(self, data: bytes) -> int: ...

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


SerialFactory = Callable[..., SerialPort]


def stable_serial_device(device: str | Path) -> Path:
    """Validate a direct, stable Linux serial-by-id path without opening it."""
    path = Path(device)
    if not path.is_absolute() or path.parent != SERIAL_BY_ID_DIRECTORY or path.name in {"", ".", ".."}:
        raise ValueError(
            "ESP32 serial device must be a direct /dev/serial/by-id/... path; "
            "do not use ttyUSB* or ttyACM*"
        )
    return path


def _pyserial_factory(**kwargs: Any) -> SerialPort:
    """Import pyserial only when real hardware is requested."""
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SerialTransportError("pyserial is not installed") from exc
    try:
        return serial.Serial(**kwargs)
    except serial.SerialException as exc:
        raise SerialTransportError(str(exc)) from exc


class PySerialTransport:
    """A direct ESP32 serial port with no queued reads or writes.

    ``timeout=0`` and ``write_timeout=0`` keep every port operation
    nonblocking.  The bridge sends complete newline-delimited messages.  If
    the operating system reports a short write, this class never attempts to
    complete that old message: it closes the link so the bridge fails safe.
    """

    def __init__(
        self,
        device: str | Path,
        *,
        baudrate: int = BAUD_RATE,
        max_read_bytes: int = DEFAULT_MAX_READ_BYTES,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        self.device = stable_serial_device(device)
        if baudrate != BAUD_RATE:
            raise ValueError(f"motor serial baudrate must be {BAUD_RATE}")
        if max_read_bytes <= 0:
            raise ValueError("max_read_bytes must be positive")
        self.baudrate = baudrate
        self.max_read_bytes = max_read_bytes
        self._serial_factory = serial_factory or _pyserial_factory
        self._port: SerialPort | None = None
        self.last_error: str | None = None

    @property
    def connected(self) -> bool:
        return self._port is not None and bool(self._port.is_open)

    def open(self) -> bool:
        """Open once, with all pyserial waits and flow control disabled."""
        if self.connected:
            return True
        self.close()
        try:
            port = self._serial_factory(
                port=str(self.device),
                baudrate=self.baudrate,
                timeout=0,
                write_timeout=0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            if not bool(port.is_open):
                raise SerialTransportError("serial port opened closed")
        except (OSError, ValueError) as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            self._port = None
            return False
        self._port = port
        self.last_error = None
        return True

    def _require_port(self) -> SerialPort:
        if not self.connected:
            self._close_port()
            raise SerialTransportError("serial port is disconnected")
        assert self._port is not None
        return self._port

    def _close_port(self) -> None:
        port, self._port = self._port, None
        if port is None:
            return
        try:
            port.close()
        except OSError:
            # The port is already unusable.  Closing must stay best-effort.
            pass

    def _fail(self, action: str, exc: BaseException) -> None:
        self.last_error = f"{action}: {exc}" if str(exc) else action
        self._close_port()
        raise SerialTransportError(self.last_error) from exc

    def write(self, data: bytes) -> None:
        """Write exactly one complete packet or fail closed on a short write."""
        if not data:
            raise ValueError("serial write must not be empty")
        port = self._require_port()
        try:
            written = port.write(data)
        except Exception as exc:
            self._fail("serial write failed", exc)
        if written != len(data):
            self._fail("partial serial write", SerialTransportError(f"{written}/{len(data)} bytes"))

    def read(self) -> bytes:
        """Read at most one bounded chunk, never waiting for more bytes."""
        port = self._require_port()
        try:
            waiting = int(port.in_waiting)
            if waiting <= 0:
                return b""
            data = port.read(min(waiting, self.max_read_bytes))
        except Exception as exc:
            self._fail("serial read failed", exc)
        if not isinstance(data, bytes):
            self._fail("serial read returned non-bytes", TypeError(type(data).__name__))
        return data

    def close(self) -> None:
        self._close_port()
