"""World-clock controls for Gazebo only; all motor velocities stay unchanged."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import math
import time


RATES = (1, 2, 3)
WORLD = "/world/indoor_maze"  # Both supported SDF files use this world name.


class SimulationPlayback:
    """One bounded service request and one isolated Gazebo statistics reader.

    Neither subprocess runs on the control tick or blocks the websocket, so
    Stop and the existing wall-clock command deadlines remain responsive.
    """

    def __init__(self) -> None:
        self.target = None
        self.error = None
        self._stats = None
        self._received = None
        self._clock_sample = None
        self._actual = None
        self._request = None
        self._reader = None

    @property
    def pending(self) -> bool:
        return self._request is not None and not self._request.done()

    def state(self) -> dict:
        fresh = self._received is not None and time.monotonic() - self._received < 3
        return {"target": self.target, "pending": self.pending, "error": self.error,
                "actual": self._stats["actual"] if fresh else None,
                "paused": self._stats["paused"] if fresh else None,
                "sim_seconds": self._stats["sim_seconds"] if fresh else None}

    def start(self) -> None:
        self._reader = asyncio.create_task(self._read_stats())

    async def close(self) -> None:
        for task in (self._request, self._reader):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    def request(self, rate: object) -> bool:
        if type(rate) not in (int, float) or rate not in RATES or self.pending:
            return False
        self.error = None
        self._request = asyncio.create_task(self._apply(int(rate)))
        return True

    async def _apply(self, rate: int) -> None:
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "gz", "service", "-s", WORLD + "/set_physics/blocking",
                "--reqtype", "gz.msgs.Physics", "--reptype", "gz.msgs.Boolean",
                "--timeout", "1500", "--req", f"real_time_factor: {rate}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=4096,
            )
            async with asyncio.timeout(2.5):
                output, _ = await process.communicate()
            if process.returncode != 0 or output.strip() != b"data: true":
                raise RuntimeError("Gazebo did not confirm the playback change.")
            self.target = rate
        except (OSError, RuntimeError, TimeoutError) as exc:
            self.error = str(exc) or "Gazebo playback request timed out."
        finally:
            await self._terminate(process)

    @staticmethod
    async def _terminate(process) -> None:
        if process is not None and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    def _accept_stats(self, raw: bytes) -> None:
        data = json.loads(raw)
        if not isinstance(data, dict) or not all(isinstance(data.get(key), dict) for key in ("simTime", "realTime")):
            raise ValueError("Invalid simulation statistics")
        paused = data.get("paused", False)
        if type(paused) is not bool:
            raise ValueError("Invalid pause state")
        clocks = []
        for key in ("simTime", "realTime"):
            clock = data[key]
            value = int(clock.get("sec", 0)) + int(clock.get("nsec", 0)) / 1e9
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid simulation clock")
            clocks.append(value)
        sim, real = clocks
        now = time.monotonic()
        previous = self._clock_sample
        if (previous is None or sim < previous[0] or real < previous[1]
                or self._received is None or now - self._received >= 3):
            self._actual = None
            self._clock_sample = (sim, real)
        elif real - previous[1] >= 1:
            # Gazebo's instantaneous RTF spikes with scheduling jitter. Use
            # its native elapsed clocks over a one-second window instead.
            self._actual = (sim - previous[0]) / (real - previous[1])
            self._clock_sample = (sim, real)
        if paused:
            self._actual = 0.0
            self._clock_sample = (sim, real)
        self._stats = {"actual": self._actual, "paused": paused, "sim_seconds": sim}
        self._received = now

    async def _read_stats(self) -> None:
        while True:
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    "gz", "topic", "-e", "--json-output", "-t", WORLD + "/stats",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    limit=4096,
                )
                while True:
                    async with asyncio.timeout(4):
                        line = await process.stdout.readline()
                    if not line:
                        break
                    self._accept_stats(line)
            except (OSError, ValueError, TypeError, TimeoutError):
                pass  # Stale statistics are displayed as unavailable, never as zero.
            finally:
                await self._terminate(process)
            await asyncio.sleep(1)
