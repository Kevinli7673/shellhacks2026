import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import WebSocketDisconnect

from rescuebot.gazebo_backend import GazeboMotorBackend
from rescuebot.service import RobotControlService
from rescuebot.simulation_playback import SimulationPlayback
from rescuebot.web import create_app


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_is_bounded_and_only_confirms_success(self):
        playback = SimulationPlayback()
        process = Mock(returncode=0, communicate=AsyncMock(return_value=(b"data: true\n", None)))
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            for invalid in (True, "2", None, 0, -1, 1.5, 4, float("nan"), float("inf")):
                self.assertFalse(playback.request(invalid))
            self.assertTrue(playback.request(2))
            self.assertFalse(playback.request(3))
            await playback._request
            self.assertEqual(playback.state()["target"], 2)
            args = spawn.call_args.args
            self.assertEqual(args[-1], "real_time_factor: 2")
            self.assertNotIn("max_step_size", " ".join(args))
            process.communicate.return_value = (b"data: false\n", None)
            self.assertTrue(playback.request(3))
            await playback._request
            self.assertEqual(playback.target, 2)
            self.assertIsNotNone(playback.error)

    async def test_cancel_kills_outstanding_request_and_never_confirms_it(self):
        started = asyncio.Event()

        async def communicate():
            started.set()
            await asyncio.Event().wait()

        playback = SimulationPlayback()
        process = Mock(returncode=None, communicate=communicate, wait=AsyncMock())
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            playback.request(3)
            await started.wait()
            await playback.close()
        process.kill.assert_called_once()
        self.assertIsNone(playback.target)

    async def test_timeout_kills_request_and_allows_explicit_retry(self):
        playback = SimulationPlayback()
        process = Mock(returncode=None, communicate=AsyncMock(side_effect=TimeoutError), wait=AsyncMock())
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertTrue(playback.request(2))
            await playback._request
            self.assertFalse(playback.pending)
            self.assertIsNone(playback.target)
            self.assertIn("timed out", playback.error)
            process.kill.assert_called_once()

    async def test_failed_launch_and_stale_statistics_are_not_success_or_zero(self):
        playback = SimulationPlayback()
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("gz missing"))):
            playback.request(2)
            await playback._request
        self.assertIsNone(playback.target)
        self.assertIn("gz missing", playback.error)
        playback._accept_stats(b'{"simTime":{"sec":"18"},"realTime":{"sec":"10"}}')
        self.assertIsNone(playback.state()["actual"])
        playback._accept_stats(b'{"simTime":{"sec":"20","nsec":500000000},"realTime":{"sec":"11"},"realTimeFactor":9}')
        self.assertEqual(playback.state()["actual"], 2.5)
        self.assertEqual(playback.state()["sim_seconds"], 20.5)
        playback._received -= 4
        self.assertIsNone(playback.state()["actual"])
        with self.assertRaises(ValueError):
            playback._accept_stats(b'{"simTime":{"sec":"NaN"},"realTime":{}}')

    async def test_pause_and_world_reset_do_not_report_a_stale_rate(self):
        playback = SimulationPlayback()
        playback._accept_stats(b'{"simTime":{"sec":"5"},"realTime":{"sec":"2"}}')
        playback._accept_stats(b'{"simTime":{"sec":"8"},"realTime":{"sec":"3"}}')
        self.assertEqual(playback.state()["actual"], 3)
        playback._accept_stats(b'{"simTime":{"sec":"8"},"realTime":{"sec":"3"},"paused":true}')
        self.assertEqual(playback.state()["actual"], 0)
        playback._accept_stats(b'{"simTime":{},"realTime":{}}')
        self.assertIsNone(playback.state()["actual"])


class Socket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    async def accept(self):
        pass

    async def send_json(self, message):
        self.sent.append(message)

    async def receive_json(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise WebSocketDisconnect()


class PlaybackWebTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, service, playback, messages):
        with patch("rescuebot.web.SimulationPlayback", return_value=playback) as factory:
            app = create_app(service)
        socket = Socket(messages)
        route = next(route for route in app.routes if route.path == "/ws/control")
        await route.endpoint(socket)
        return socket.sent, factory

    async def test_owner_and_disarmed_gate_and_stop_during_pending_request(self):
        with tempfile.TemporaryDirectory() as directory:
            service = RobotControlService(backend=GazeboMotorBackend(directory + "/sim.sock"))
            self.addCleanup(service.close)
            playback = Mock(pending=False, state=Mock(return_value={}), request=Mock(return_value=True))
            def request(_rate):
                playback.pending = True
                return True
            playback.request.side_effect = request
            messages, _ = await self.exercise(service, playback, [
                {"type": "simulation_playback", "rate": 2},  # no ownership
                {"type": "claim"}, {"type": "enable"},
                {"type": "simulation_playback", "rate": 2},  # driving enabled
                {"type": "stop"}, {"type": "simulation_playback", "rate": 2},
                {"type": "enable"}, {"type": "stop"},
            ])
            responses = [m for m in messages if m["type"] != "state"]
            self.assertEqual([m["accepted"] for m in responses], [False, True, True, False, True, True, False, True])
            playback.request.assert_called_once_with(2)
            self.assertFalse(service.control.armed)

    async def test_non_gazebo_keeps_existing_message_and_state_behavior(self):
        service = RobotControlService()
        messages, factory = await self.exercise(service, Mock(), [
            {"type": "claim"}, {"type": "enable"}, {"type": "simulation_playback", "rate": 3},
        ])
        factory.assert_not_called()
        self.assertEqual(messages[-2]["type"], "error")
        self.assertEqual(messages[-1]["data"]["control"]["fault"], "invalid_browser_message")
        for message in messages:
            if message["type"] == "state":
                self.assertNotIn("simulation_playback", message["data"])
