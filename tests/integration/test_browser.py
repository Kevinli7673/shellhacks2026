"""Real browser against the real dashboard and motor-bridge processes.

Drives headless Chrome over the DevTools protocol with real key and mouse
events and checks the server's state after each step. Opt-in:
RESCUEBOT_INTEGRATION=1. Needs Google Chrome or Chromium (set RESCUEBOT_CHROME
to its path if it is not found) and the websockets package, which ships with
uvicorn[standard].
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request


APP_DIR = Path(__file__).parents[2] / "app"
CHROME_CANDIDATES = [
    os.environ.get("RESCUEBOT_CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]


def _chrome() -> str | None:
    return next((c for c in CHROME_CANDIDATES if c and Path(c).exists()), None)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Tab:
    def __init__(self, ws) -> None:
        self.ws, self.n, self.exceptions = ws, 0, []

    async def call(self, method: str, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})
            if msg.get("method") == "Runtime.exceptionThrown":
                self.exceptions.append(msg)

    async def js(self, expr: str):
        result = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return result.get("result", {}).get("value")

    async def key(self, kind: str, code: str, key: str, vk: int) -> None:
        await self.call("Input.dispatchKeyEvent", type=kind, code=code, key=key, windowsVirtualKeyCode=vk)

    async def click(self, selector: str) -> None:
        x, y = await self.js(
            f"(() => {{ const r = document.querySelector('{selector}').getBoundingClientRect();"
            " return [r.x + r.width / 2, r.y + r.height / 2]; })()"
        )
        for kind in ("mousePressed", "mouseReleased"):
            await self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    async def text(self, element_id: str) -> str:
        return await self.js(f"document.getElementById('{element_id}').textContent")


@unittest.skipUnless(os.environ.get("RESCUEBOT_INTEGRATION") == "1", "set RESCUEBOT_INTEGRATION=1")
class BrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chrome = _chrome()
        if self.chrome is None:
            self.skipTest("Chrome/Chromium not found (set RESCUEBOT_CHROME)")
        try:
            import websockets  # noqa: F401
        except ImportError:
            self.skipTest("websockets not installed")

    def test_operator_flows_in_a_real_browser(self) -> None:
        self.assertEqual(asyncio.run(self._scenario()), [])

    async def _scenario(self) -> list[str]:
        import websockets

        failures: list[str] = []
        port, cdp = _free_port(), _free_port()
        base = f"http://127.0.0.1:{port}"
        run_dir = tempfile.mkdtemp(prefix="rb", dir="/tmp")
        profile = tempfile.mkdtemp(prefix="rb-chrome-")
        env = dict(os.environ, RESCUEBOT_RUN_DIR=run_dir, PYTHONPATH=str(APP_DIR))

        def check(name: str, ok: bool, detail: str = "") -> None:
            if not ok:
                failures.append(f"{name}: {detail}")

        def api() -> dict:
            return json.load(urllib.request.urlopen(base + "/api/state"))

        def start_bridge():
            return subprocess.Popen([sys.executable, "-m", "rescuebot.motor_bridge"], env=env)

        def start_dashboard():
            code = ("import uvicorn, rescuebot.web as w; "
                    f"uvicorn.run(w.create_app(motor_backend='bridge'), host='127.0.0.1', port={port}, log_level='error')")
            return subprocess.Popen([sys.executable, "-c", code], env=env)

        async def wait_for(pred, timeout: float = 3.0) -> bool:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    if pred(api()):
                        return True
                except OSError:
                    pass
                await asyncio.sleep(0.05)
            return False

        async def wait_ui(tab: _Tab, element_id: str, pred, timeout: float = 2.0) -> bool:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if pred(await tab.text(element_id)):
                    return True
                await asyncio.sleep(0.05)
            return False

        async def open_tab() -> _Tab:
            request = urllib.request.Request(f"http://127.0.0.1:{cdp}/json/new?{base}/", method="PUT")
            target = json.load(urllib.request.urlopen(request))
            tab = _Tab(await websockets.connect(target["webSocketDebuggerUrl"], max_size=None))
            for method in ("Runtime.enable", "Page.enable"):
                await tab.call(method)
            await tab.call("Emulation.setFocusEmulationEnabled", enabled=True)
            return tab

        bridge, dash = start_bridge(), start_dashboard()
        chrome = subprocess.Popen(
            [self.chrome, "--headless=new", "--disable-gpu", f"--remote-debugging-port={cdp}", f"--user-data-dir={profile}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(150):
                try:
                    urllib.request.urlopen(base + "/api/state")
                    urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json/version")
                    break
                except OSError:
                    time.sleep(0.1)
            await wait_for(lambda s: s["motor"]["healthy"], 5.0)
            a = await open_tab()
            await asyncio.sleep(1.0)
            check("fonts loaded", await a.js("document.fonts.check('700 16px B612')") is True)

            await a.click("#enable-button")
            check("enable arms through firmware", await wait_for(lambda s: s["motor"]["firmware_armed"] and not s["control"]["arming"]))
            check("UI shows armed", await wait_ui(a, "driving-status", lambda t: "Armed" in t), await a.text("driving-status"))

            await a.key("keyDown", "KeyW", "w", 87)
            check("W drives", await wait_for(lambda s: s["motor"]["wheels"]["fl"] > 0), str(api()["motor"]["wheels"]))
            await asyncio.sleep(1.0)
            check("held key stays armed for 1 s", api()["control"]["armed"], str(api()["control"]["fault"]))
            check("keycap lights while held", await a.js("document.querySelector('[data-key=KeyW]').classList.contains('lit')") is True)
            await a.key("keyUp", "KeyW", "w", 87)
            check("release zeroes wheels, stays armed",
                  await wait_for(lambda s: s["motor"]["wheels"]["fl"] == 0 and s["control"]["armed"]))

            before = api()["control"]["speed_percent"]
            await a.key("keyDown", "ArrowUp", "ArrowUp", 38)
            await a.key("keyUp", "ArrowUp", "ArrowUp", 38)
            await asyncio.sleep(0.3)
            s = api()
            check("speed key alone never moves", s["control"]["speed_percent"] == before + 10 and s["motor"]["wheels"]["fl"] == 0)

            await a.key("keyDown", "KeyD", "d", 68)
            await wait_for(lambda s: s["motor"]["wheels"]["fl"] > 0)
            await a.key("keyDown", "Space", " ", 32)
            check("Space stops and disarms firmware", await wait_for(lambda s: not s["motor"]["firmware_armed"], 1.0))
            await a.key("keyUp", "Space", " ", 32)
            await a.key("keyUp", "KeyD", "d", 68)
            check("stop reason shown", await wait_ui(a, "fault-message", lambda t: "operator" in t.lower()))

            bridge.kill()
            bridge.wait()
            check("idle UI shows bridge unavailable without input",
                  await wait_ui(a, "node-motor", lambda t: t == "Unavailable", 3.0), await a.text("node-motor"))
            bridge = start_bridge()
            check("idle UI recovers when the bridge returns",
                  await wait_ui(a, "node-motor", lambda t: t.startswith("Connected"), 5.0), await a.text("node-motor"))

            await a.click("#enable-button")
            await wait_for(lambda s: s["motor"]["firmware_armed"])
            await a.key("keyDown", "KeyW", "w", 87)
            await a.js("window.dispatchEvent(new Event('blur'))")
            check("blur stops and disables", await wait_for(lambda s: not s["control"]["armed"], 1.0))
            await a.key("keyUp", "KeyW", "w", 87)

            await a.click("#enable-button")
            await wait_for(lambda s: s["motor"]["firmware_armed"])
            await a.js("Object.defineProperty(document, 'hidden', {value: true, configurable: true});"
                       " document.dispatchEvent(new Event('visibilitychange'))")
            check("hidden tab stops and disables", await wait_for(lambda s: not s["control"]["armed"], 1.0))
            await a.js("delete document.hidden")

            await a.click("#enable-button")
            await wait_for(lambda s: s["motor"]["firmware_armed"])
            b = await open_tab()
            await asyncio.sleep(1.0)
            check("second browser read-only", (await b.text("enable-button")) == "Read-only")
            check("read-only viewer sees armed state", await wait_ui(b, "driving-status", lambda t: "Armed" in t), await b.text("driving-status"))
            await b.key("keyDown", "KeyW", "w", 87)
            await asyncio.sleep(0.4)
            check("read-only keys cannot drive", api()["motor"]["wheels"]["fl"] == 0)
            await b.key("keyUp", "KeyW", "w", 87)
            await b.click("#stop-button")
            check("read-only Stop disarms", await wait_for(lambda s: not s["control"]["armed"], 1.0))
            check("viewer UI follows the stop", await wait_ui(b, "driving-status", lambda t: t == "Disabled"), await b.text("driving-status"))

            await a.click("#enable-button")
            await wait_for(lambda s: s["motor"]["firmware_armed"])
            dash.kill()
            dash.wait()
            check("UI shows disconnected", await wait_ui(a, "node-browser", lambda t: t == "Disconnected", 3.0))
            dash = start_dashboard()
            await asyncio.sleep(2.5)
            s = api()
            check("reconnect never re-arms", not s["control"]["armed"] and not s["motor"]["firmware_armed"], str(s["control"]["fault"]))
            check("browser reconnects", await wait_ui(a, "node-browser", lambda t: t == "Connected", 3.0))
            check("no JS exceptions", not (a.exceptions + b.exceptions), str((a.exceptions + b.exceptions)[:1]))
        finally:
            for proc in (chrome, dash, bridge):
                proc.terminate()
            for proc in (chrome, dash, bridge):
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            shutil.rmtree(run_dir, ignore_errors=True)
            shutil.rmtree(profile, ignore_errors=True)
        return failures


if __name__ == "__main__":
    unittest.main()
