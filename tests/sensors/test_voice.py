"""Voice alerts: bearings from boxes, speaker queue/cooldown, failures, dashboard wiring."""

from __future__ import annotations

import threading
import time
import unittest

from fastapi.testclient import TestClient

from rescuebot import voice
from rescuebot.replay_camera import MockCameraBackend
from rescuebot.voice import CameraVoice, Speaker, alert_text, with_bearings, x_to_bearing
from rescuebot.web import create_app


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def person(x, width=0.2, confidence=0.8):
    return {"label": "person", "confidence": confidence,
            "bbox": {"x": x, "y": 0.1, "width": width, "height": 0.6}}


class FakeSynth:
    """Records phrases instead of calling ElevenLabs; can fail or block."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.release = threading.Event()
        self.release.set()

    def __call__(self, text, voice_id, model):
        self.calls.append((text, voice_id, model))
        self.release.wait(5)
        if self.error is not None:
            raise self.error
        return f"/fake/{text}.mp3"


class FakeCamera:
    def __init__(self, status):
        self.current = status

    def status(self):
        return self.current


class RecordingSpeaker:
    def __init__(self):
        self.said = []
        self.closed = False

    def say(self, text):
        self.said.append(text)
        return True

    def close(self, timeout=10):
        self.closed = True


def make_speaker(synth=None, played=None, fallback_calls=None, fallback_result=True, **kwargs):
    played = [] if played is None else played
    fallback_calls = [] if fallback_calls is None else fallback_calls

    def fallback(text):
        fallback_calls.append(text)
        return fallback_result

    return Speaker(synthesizer=synth or FakeSynth(), player=played.append, fallback=fallback,
                   log=lambda msg: None, **kwargs)


class BearingTests(unittest.TestCase):
    def test_x_to_bearing_matches_rescue_sensors(self):
        self.assertAlmostEqual(x_to_bearing(0.5, 66), 0.0)
        self.assertAlmostEqual(x_to_bearing(1.0, 66), 33.0)
        self.assertAlmostEqual(x_to_bearing(0.0, 66), -33.0)

    def test_box_positions_give_left_right_and_ahead(self):
        cases = {0.4: "Person detected ahead.",   # center 0.5
                 0.0: "Person detected on the left.",   # center 0.1
                 0.8: "Person detected on the right."}  # center 0.9
        for x, expected in cases.items():
            with self.subTest(x=x):
                self.assertEqual(alert_text(with_bearings([person(x)])), expected)

    def test_uses_box_center_not_left_edge(self):
        # Left edge at 0.3 would be "on the left"; the center (0.5) is ahead.
        self.assertEqual(alert_text(with_bearings([person(0.3, width=0.4)])), "Person detected ahead.")

    def test_does_not_modify_input_and_keeps_existing_bearing(self):
        det = person(0.0)
        with_bearings([det])
        self.assertNotIn("bearing_deg", det)
        self.assertEqual(with_bearings([{**det, "bearing_deg": 25}])[0]["bearing_deg"], 25)

    def test_missing_or_bad_box_passes_through(self):
        dets = with_bearings([{"label": "person"}, {"label": "person", "bbox": {"x": "?"}}])
        self.assertNotIn("bearing_deg", dets[0])
        self.assertNotIn("bearing_deg", dets[1])


class SpeakerTests(unittest.TestCase):
    def test_speaks_through_synthesizer_and_player(self):
        synth, played = FakeSynth(), []
        speaker = make_speaker(synth, played)
        self.assertTrue(speaker.say("Person detected ahead."))
        self.assertTrue(wait_for(lambda: played == ["/fake/Person detected ahead..mp3"]))
        self.assertEqual(synth.calls, [("Person detected ahead.", voice.DEFAULT_VOICE, "eleven_flash_v2_5")])
        speaker.close(2)

    def test_cooldown_skips_repeats_until_it_expires(self):
        now = [100.0]
        speaker = make_speaker(cooldown=8.0, clock=lambda: now[0])
        self.assertTrue(speaker.say("Person detected ahead."))
        now[0] += 7.9
        self.assertFalse(speaker.say("Person detected ahead."))
        self.assertTrue(speaker.say("Person detected on the left."))  # different phrase is not blocked
        now[0] += 0.2
        self.assertTrue(speaker.say("Person detected ahead."))
        speaker.close(2)

    def test_queue_drops_extra_alerts(self):
        synth = FakeSynth()
        synth.release.clear()  # hold the first phrase "speaking"
        speaker = make_speaker(synth, max_queue=2)
        self.assertTrue(speaker.say("one"))
        self.assertTrue(wait_for(lambda: len(synth.calls) == 1))
        self.assertTrue(speaker.say("two"))
        self.assertTrue(speaker.say("three"))
        self.assertFalse(speaker.say("four"))
        self.assertFalse(speaker.say(None))
        synth.release.set()
        self.assertTrue(wait_for(lambda: [c[0] for c in synth.calls] == ["one", "two", "three"]))
        speaker.close(2)

    def test_network_failure_falls_back_and_never_raises(self):
        synth, played, fallback_calls = FakeSynth(error=ConnectionError("no network")), [], []
        speaker = make_speaker(synth, played, fallback_calls)
        self.assertTrue(speaker.say("Person detected ahead."))
        self.assertTrue(wait_for(lambda: fallback_calls == ["Person detected ahead."]))
        self.assertEqual(played, [])
        self.assertTrue(speaker.say("Person detected on the left."))  # thread still alive
        self.assertTrue(wait_for(lambda: len(fallback_calls) == 2))
        self.assertTrue(speaker.thread.is_alive())
        speaker.close(2)

    def test_failure_without_fallback_stays_silent(self):
        fallback_calls = []
        speaker = make_speaker(FakeSynth(error=OSError("dns")), [], fallback_calls, fallback_result=False)
        speaker.say("Person detected ahead.")
        self.assertTrue(wait_for(lambda: fallback_calls == ["Person detected ahead."]))
        self.assertTrue(speaker.thread.is_alive())
        speaker.close(2)

    def test_broken_player_does_not_kill_thread(self):
        synth = FakeSynth()

        def broken_player(path):
            raise FileNotFoundError("ffplay")

        speaker = Speaker(synthesizer=synth, player=broken_player, fallback=lambda text: True,
                          log=lambda msg: None)
        speaker.say("one")
        speaker.say("two")
        self.assertTrue(wait_for(lambda: len(synth.calls) == 2))
        self.assertTrue(speaker.thread.is_alive())
        speaker.close(2)

    def test_prepare_failure_never_raises(self):
        synth = FakeSynth(error=ConnectionError("no network"))
        speaker = make_speaker(synth)
        speaker.prepare(["a", "b"])
        self.assertTrue(wait_for(lambda: len(synth.calls) == 1))
        speaker.close(2)


class CameraVoiceTests(unittest.TestCase):
    def test_poll_speaks_bearing_from_box(self):
        camera = FakeCamera({"status": "online", "detections": [person(0.8)]})
        speaker = RecordingSpeaker()
        camera_voice = CameraVoice(camera.status, speaker)
        self.assertEqual(camera_voice.poll_once(), "Person detected on the right.")
        camera.current = {"status": "online", "detections": [person(0.0)]}
        camera_voice.poll_once()
        camera.current = {"status": "online", "detections": []}
        self.assertIsNone(camera_voice.poll_once())
        self.assertEqual(speaker.said, ["Person detected on the right.", "Person detected on the left."])

    def test_poll_ignores_offline_or_stale_camera(self):
        speaker = RecordingSpeaker()
        for state in ("offline", "stale", "waiting"):
            camera = FakeCamera({"status": state, "detections": [person(0.4)]})
            self.assertIsNone(CameraVoice(camera.status, speaker).poll_once())
        self.assertEqual(speaker.said, [])

    def test_thread_survives_camera_errors_and_closes(self):
        calls = []

        def status():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("camera glitch")
            return {"status": "online", "detections": [person(0.4)]}

        speaker = RecordingSpeaker()
        camera_voice = CameraVoice(status, speaker, interval_s=0.01, log=lambda msg: None)
        camera_voice.start()
        self.assertTrue(wait_for(lambda: speaker.said))
        camera_voice.close()
        self.assertFalse(camera_voice._thread.is_alive())
        self.assertTrue(speaker.closed)


class DashboardVoiceTests(unittest.TestCase):
    def test_voice_off_by_default(self):
        self.assertIsNone(create_app().state.camera_voice)

    def test_dashboard_starts_and_closes_voice_and_stop_still_works(self):
        speaker = RecordingSpeaker()
        camera = MockCameraBackend()
        camera_voice = CameraVoice(lambda: {"status": "online", "detections": [person(0.8)]},
                                   speaker, interval_s=0.01)
        app = create_app(camera=camera, camera_voice=camera_voice)
        with TestClient(app) as client:
            self.assertTrue(wait_for(lambda: "Person detected on the right." in speaker.said))
            with client.websocket_connect("/ws/control") as ws:
                ws.receive_json()
                ws.send_json({"type": "stop"})
                self.assertEqual(ws.receive_json(), {"type": "stop", "accepted": True})
        self.assertTrue(speaker.closed)
        self.assertFalse(camera_voice._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
