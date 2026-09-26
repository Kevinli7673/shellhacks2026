"""Pi motor bridge -> real pyserial -> virtual serial port -> real firmware logic.

The firmware's host-compilable decision logic (controller, codec, session
guard, mixing, line reader) is compiled with hostfw/main_host.cpp and attached
to a pseudo-terminal, so the bridge talks to it exactly as it would talk to the
ESP32-S2 over USB. Opt-in: RESCUEBOT_INTEGRATION=1. Needs the firmware/ tree
(present on integrated branches), clang++ or g++, pyserial, and ArduinoJson
headers (run `pio test -e native` in firmware/ once, or set
RESCUEBOT_ARDUINOJSON_SRC to ArduinoJson's src/ directory).
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest



ROOT = Path(__file__).parents[2]
FIRMWARE = ROOT / "firmware"
HARNESS = Path(__file__).parent / "hostfw" / "main_host.cpp"
FIRMWARE_SOURCES = ["controller.cpp", "protocol_codec.cpp", "session_guard.cpp", "mixing.cpp", "line_reader.cpp"]
STOPPED = {"fl": 0, "fr": 0, "rl": 0, "rr": 0}


def _arduinojson_src() -> Path | None:
    candidates = [os.environ.get("RESCUEBOT_ARDUINOJSON_SRC", "")]
    candidates += [str(p) for p in FIRMWARE.glob(".pio/libdeps/*/ArduinoJson/src")]
    for candidate in candidates:
        if candidate and (Path(candidate) / "ArduinoJson.h").exists():
            return Path(candidate)
    return None


class _Firmware:
    """The compiled firmware logic on the pty master, like a USB device."""

    def __init__(self, binary: Path, master: int, ceiling: int = 255) -> None:
        self.proc = subprocess.Popen(
            [str(binary), str(ceiling)], stdin=master, stdout=master, stderr=subprocess.PIPE, text=True
        )
        self.motors: dict | None = None
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            match = re.match(r"MOTORS armed=(\d) fl=(-?\d+) fr=(-?\d+) rl=(-?\d+) rr=(-?\d+)", line)
            if match:
                armed, fl, fr, rl, rr = map(int, match.groups())
                self.motors = {"armed": bool(armed), "wheels": {"fl": fl, "fr": fr, "rl": rl, "rr": rr}}

    def stop(self) -> None:
        self.proc.kill()
        self.proc.wait()


class _Arbiter:
    def __init__(self) -> None:
        self.seq = 0

    def cmd(self, kind: str, forward: float = 0.0, sideways: float = 0.0, speed: int = 100) -> bytes:
        from rescuebot.bridge_ipc import ArbiterCommand

        self.seq += 1
        return ArbiterCommand(kind, "integration", self.seq, time.monotonic() + 0.25, forward, sideways, 0.0, speed).encode()


def _run(bridge, seconds: float, make=None):
    """Step the bridge at 100 Hz, sending the arbiter's intent every 50 ms."""
    end, last_send, status = time.monotonic() + seconds, 0.0, None
    while time.monotonic() < end:
        now = time.monotonic()
        datagrams = []
        if make and now - last_send >= 0.05:
            datagrams, last_send = [make()], now
        status = bridge.step(datagrams, now)
        time.sleep(0.01)
    return status


@unittest.skipUnless(os.environ.get("RESCUEBOT_INTEGRATION") == "1", "set RESCUEBOT_INTEGRATION=1")
class FirmwareLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (FIRMWARE / "src" / "controller.cpp").exists():
            raise unittest.SkipTest("firmware/ tree not present (dashboard-only checkout)")
        try:
            import rescuebot.serial_transport  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("rescuebot.serial_transport not present on this branch") from exc
        compiler = shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("no C++ compiler")
        json_src = _arduinojson_src()
        if json_src is None:
            raise unittest.SkipTest("ArduinoJson headers not found; run `pio test -e native` in firmware/")
        try:
            import serial  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("pyserial not installed") from exc
        cls.build_dir = tempfile.mkdtemp(prefix="rb-hostfw-")
        cls.binary = Path(cls.build_dir) / "hostfw"
        command = [compiler, "-std=c++17", "-O1", "-Wall", "-Wextra",
                   "-I", str(FIRMWARE / "include"), "-I", str(json_src),
                   *[str(FIRMWARE / "src" / name) for name in FIRMWARE_SOURCES], str(HARNESS), "-o", str(cls.binary)]
        build = subprocess.run(command, capture_output=True, text=True)
        if build.returncode != 0:
            raise AssertionError("firmware harness failed to compile:\n" + build.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.build_dir, ignore_errors=True)

    def test_bridge_to_firmware_scenarios(self) -> None:
        import serial

        from rescuebot.motor_bridge import MotorBridge
        from rescuebot.serial_transport import PySerialTransport

        failures: list[str] = []

        def check(name: str, ok: bool, detail: str = "") -> None:
            if not ok:
                failures.append(f"{name}: {detail}")

        master, slave = os.openpty()
        slave_path = os.ttyname(slave)
        fw = _Firmware(self.binary, master)
        transport = PySerialTransport(
            "/dev/serial/by-id/usb-virtual-esp32-if00",
            serial_factory=lambda **kw: serial.Serial(**{**kw, "port": slave_path}),
        )
        bridge = MotorBridge(transport)
        arb = _Arbiter()
        try:
            time.sleep(0.2)
            s = _run(bridge, 0.4, lambda: arb.cmd("stop"))
            check("connect disarmed over pyserial", s.transport_connected and not s.armed, f"fault={s.fault}")
            check("IMU telemetry arrives", s.imu is not None and s.imu["available"], str(s.imu))

            bridge.step([arb.cmd("arm")], time.monotonic())
            s = _run(bridge, 0.3, lambda: arb.cmd("drive"))
            check("arm confirmed by firmware", s.armed and fw.motors and fw.motors["armed"], str(fw.motors))

            s = _run(bridge, 0.4, lambda: arb.cmd("drive", forward=1.0))
            check("W: all wheels +100", fw.motors["wheels"] == {"fl": 100, "fr": 100, "rl": 100, "rr": 100}, str(fw.motors))
            check("bridge reports acknowledged wheels", s.wheels == fw.motors["wheels"] and s.ack_age_ms is not None and s.ack_age_ms < 120, f"{s.wheels} ack={s.ack_age_ms}")

            _run(bridge, 0.3, lambda: arb.cmd("drive", sideways=1.0, speed=60))
            check("D: right strafe signs", fw.motors["wheels"] == {"fl": 60, "fr": -60, "rl": -60, "rr": 60}, str(fw.motors))

            bridge.step([arb.cmd("stop")], time.monotonic())
            time.sleep(0.05)
            bridge.step([], time.monotonic())
            check("Stop disarms firmware", not fw.motors["armed"] and fw.motors["wheels"] == STOPPED, str(fw.motors))
            _run(bridge, 0.3, lambda: arb.cmd("drive", forward=1.0))
            check("no motion after Stop without re-arm", not fw.motors["armed"] and fw.motors["wheels"] == STOPPED, str(fw.motors))

            bridge.step([arb.cmd("arm")], time.monotonic())
            _run(bridge, 0.3, lambda: arb.cmd("drive", forward=1.0))
            s = _run(bridge, 0.35)
            check("dashboard silence disarms (250 ms)", not s.armed and s.fault == "arbiter_timeout" and not fw.motors["armed"], f"fault={s.fault}")

            bridge.step([arb.cmd("arm")], time.monotonic())
            _run(bridge, 0.3, lambda: arb.cmd("drive", forward=1.0))
            last_packet = bridge._last_drive_sent_at
            while fw.motors["armed"] and time.monotonic() - last_packet < 1.5:
                time.sleep(0.005)
            elapsed_ms = (time.monotonic() - last_packet) * 1000
            check("firmware watchdog cuts motors ~500 ms after the last packet", not fw.motors["armed"] and 480 <= elapsed_ms <= 650, f"{elapsed_ms:.0f} ms")
            s = _run(bridge, 0.2)
            check("bridge stays disarmed after watchdog", not s.armed, f"fault={s.fault}")

            bridge.step([arb.cmd("arm")], time.monotonic())
            _run(bridge, 0.3, lambda: arb.cmd("drive", forward=1.0))
            fw.stop()
            fw = _Firmware(self.binary, master)
            s = _run(bridge, 0.4, lambda: arb.cmd("drive", forward=1.0))
            check("reboot disarms; firmware stays stopped", not s.armed and not fw.motors["armed"] and fw.motors["wheels"] == STOPPED, f"fault={s.fault}")
            bridge.step([arb.cmd("arm")], time.monotonic())
            _run(bridge, 0.4, lambda: arb.cmd("drive", forward=1.0))
            check("explicit re-arm drives again", fw.motors["armed"] and fw.motors["wheels"]["fl"] == 100, str(fw.motors))

            fw.stop()
            os.close(master)
            master = -1
            s = _run(bridge, 0.3, lambda: arb.cmd("drive", forward=1.0))
            check("unplug: link lost, disarmed", not s.armed and not s.transport_connected, f"fault={s.fault}")
        finally:
            bridge.shutdown()
            if fw.proc.poll() is None:
                fw.stop()
            for fd in (master, slave):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        self.assertEqual(failures, [], "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
