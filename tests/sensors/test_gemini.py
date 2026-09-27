"""Gemini triage: reply parsing, REST call shape and errors, trigger rules, dashboard wiring."""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from rescuebot import gemini
from rescuebot.gemini import GeminiError, GeminiTriage, call_gemini, context_text, fetch_snapshot, normalize
from rescuebot.replay_camera import MockCameraBackend
from rescuebot.web import create_app

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
GOOD = {
    "unique_people": 1, "people_note": "", "needs_help": True, "urgency": "high", "posture": "lying",
    "hazards": ["smoke"], "summary": "A person is lying on the floor near smoke.",
    "recommended_action": "Send a team to the robot's position now.",
    "spoken_alert": "Person down ahead. Send help now.",
    "people": [{"appearance": "red hoodie, dark jeans", "where": "ahead", "match": "new", "known_id": 0}],
}


def seen(*people):
    """A Gemini reply whose people list is `people`: (match, known_id, appearance, where)."""
    return {**GOOD, "unique_people": len(people),
            "people": [{"match": m, "known_id": k, "appearance": a, "where": w} for m, k, a, w in people]}


def person(x=0.4, confidence=0.8):
    return {"label": "person", "confidence": confidence,
            "bbox": {"x": x, "y": 0.1, "width": 0.2, "height": 0.6}}


class FakeCamera:
    def __init__(self, detections=()):
        self.detections = list(detections)

    def status(self):
        return {"status": "online", "detections": self.detections}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)
        return True


class GeminiServer:
    """A local stand-in for the Gemini REST endpoint."""

    def __init__(self, status=200, body=None):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                outer.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, json.loads(self.rfile.read(length))))
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not a jpeg")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/models/{{model}}:generateContent"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def reply(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]}


class ParsingTests(unittest.TestCase):
    def test_normalize_keeps_valid_fields(self):
        self.assertEqual(normalize(GOOD), GOOD)

    def test_normalize_clamps_bad_values(self):
        out = normalize({"summary": " two\n people ", "urgency": "extreme", "posture": "flying",
                         "unique_people": "lots", "needs_help": "yes", "hazards": "fire"})
        self.assertEqual(out["summary"], "two people")
        self.assertEqual(out["urgency"], "low")
        self.assertEqual(out["posture"], "unclear")
        self.assertEqual(out["unique_people"], 0)
        self.assertFalse(out["needs_help"])  # only a real boolean true counts
        self.assertEqual(out["hazards"], [])

    def test_normalize_needs_a_summary(self):
        with self.assertRaises(GeminiError):
            normalize({"urgency": "high"})
        with self.assertRaises(GeminiError):
            normalize(["not", "an", "object"])

    def test_context_describes_where_people_are(self):
        text = context_text([person(0.8), person(0.0)])
        self.assertIn("2 person box(es), numbered left to right", text)
        self.assertLess(text.index("box 1: to the left"), text.index("box 2: to the right"))
        self.assertIn("spanning 0% to 20%", text)
        self.assertIn("no person", context_text([]))


class CallTests(unittest.TestCase):
    def test_request_shape_and_reply(self):
        server = GeminiServer(body=reply(GOOD))
        self.addCleanup(server.close)
        result = call_gemini(JPEG, "one person ahead", api_key="secret-key", models="m1", url=server.url)
        self.assertEqual(result, {**GOOD, "model": "m1"})
        path, headers, body = server.requests[0]
        self.assertEqual(path, "/models/m1:generateContent")
        self.assertEqual(headers["x-goog-api-key"], "secret-key")
        self.assertNotIn("secret-key", path)  # the key never goes in the URL
        parts = body["contents"][0]["parts"]
        self.assertEqual(base64.b64decode(parts[0]["inline_data"]["data"]), JPEG)
        self.assertIn("one person ahead", parts[1]["text"])
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")

    def test_api_error_message_is_reported(self):
        server = GeminiServer(status=400, body={"error": {"message": "API key not valid."}})
        self.addCleanup(server.close)
        with self.assertRaisesRegex(GeminiError, r"400 \(a\): API key not valid"):
            call_gemini(JPEG, "", api_key="bad", models=("a", "b"), url=server.url)
        self.assertEqual(len(server.requests), 1)  # a bad key isn't retried on the next model

    def test_busy_model_falls_back_to_the_next(self):
        busy = GeminiServer(status=503, body={"error": {"message": "high demand"}})
        self.addCleanup(busy.close)
        good = GeminiServer(body=reply(GOOD))
        self.addCleanup(good.close)
        # One URL template for both: the model name picks the server.
        url = "{model}"
        models = (busy.url.format(model="busy"), good.url.format(model="good"))
        result = call_gemini(JPEG, "", api_key="k", models=models, url=url)
        self.assertEqual(result["model"], models[1])
        with self.assertRaisesRegex(GeminiError, "503"):
            call_gemini(JPEG, "", api_key="k", models=models[:1], url=url)

    def test_blocked_or_malformed_replies(self):
        for body, pattern in (({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
                              ({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}, "valid JSON")):
            server = GeminiServer(body=body)
            self.addCleanup(server.close)
            with self.assertRaisesRegex(GeminiError, pattern):
                call_gemini(JPEG, "", api_key="k", url=server.url)

    def test_unreachable_server(self):
        with self.assertRaisesRegex(GeminiError, "Couldn't reach Gemini"):
            call_gemini(JPEG, "", api_key="k", url="http://127.0.0.1:9/{model}", timeout_s=1)

    def test_snapshot_must_be_a_jpeg(self):
        server = GeminiServer()
        self.addCleanup(server.close)
        with self.assertRaisesRegex(GeminiError, "isn't a JPEG"):
            fetch_snapshot(f"http://127.0.0.1:{server.server.server_port}/snapshot.jpg")
        with self.assertRaisesRegex(GeminiError, "No camera image"):
            fetch_snapshot("http://127.0.0.1:9/snapshot.jpg", timeout_s=1)


class TriageTests(unittest.TestCase):
    def make(self, camera, result=GOOD, error=None, speaker=None, save_dir=None):
        self.calls = []

        def analyze(jpeg, context):
            self.calls.append(context)
            if error is not None:
                raise error
            return dict(result)

        clock = Clock()
        triage = GeminiTriage(camera.status, lambda: JPEG, analyze, speaker=speaker, clock=clock,
                              save_dir=save_dir, recheck_s=30, min_gap_s=8, gone_after_s=3, log=lambda m: None)
        return triage, clock

    def test_new_person_triggers_once_then_rechecks(self):
        camera = FakeCamera([person()])
        triage, clock = self.make(camera)
        self.assertEqual(triage.poll_once(), "new person")
        clock.t += 10
        self.assertIsNone(triage.poll_once())  # still the same sighting
        clock.t += 25
        self.assertEqual(triage.poll_once(), "still in view")
        self.assertEqual(len(self.calls), 2)
        status = triage.status()
        self.assertEqual(status["state"], "watching")
        self.assertEqual(status["latest"]["urgency"], "high")
        self.assertEqual(status["latest"]["detector_people"], 1)
        self.assertEqual(len(status["history"]), 1)

    def test_person_leaving_and_returning_is_a_new_sighting(self):
        camera = FakeCamera([person()])
        triage, clock = self.make(camera)
        triage.poll_once()
        camera.detections = []
        clock.t += 1
        triage.poll_once()
        clock.t += 1
        camera.detections = [person()]
        self.assertIsNone(triage.poll_once())  # brief dropout, same person
        camera.detections = []
        clock.t += 4
        triage.poll_once()
        clock.t += 4
        triage.poll_once()
        camera.detections = [person()]
        self.assertEqual(triage.poll_once(), "new person")

    def test_min_gap_limits_calls(self):
        camera = FakeCamera([person()])
        triage, clock = self.make(camera)
        triage.poll_once()
        camera.detections = []
        clock.t += 5
        triage.poll_once()
        camera.detections = [person()]
        clock.t += 1
        self.assertIsNone(triage.poll_once())  # new arrival, but only 6 s since the last call
        self.assertEqual(len(self.calls), 1)

    def test_operator_request(self):
        camera = FakeCamera([])
        triage, clock = self.make(camera)
        self.assertIsNone(triage.poll_once())
        self.assertTrue(triage.request())
        self.assertEqual(triage.poll_once(), "operator")
        self.assertIn("no person", self.calls[0])
        self.assertFalse(triage.request())  # too soon
        self.assertFalse(triage.status()["can_ask"])
        clock.t += 9
        self.assertTrue(triage.request())

    def test_gemini_speaks_for_every_new_sighting(self):
        speaker = FakeSpeaker()
        calm = {**GOOD, "needs_help": False, "urgency": "low", "spoken_alert": "I see one person standing. They look fine."}
        triage, _ = self.make(FakeCamera([person()]), result=calm, speaker=speaker)
        triage.poll_once()
        self.assertEqual(speaker.said, [calm["spoken_alert"]])

    def test_rechecks_speak_only_when_the_answer_changes(self):
        speaker = FakeSpeaker()
        camera = FakeCamera([person()])
        triage, clock = self.make(camera, speaker=speaker)
        triage.poll_once()
        clock.t += 31
        self.assertEqual(triage.poll_once(), "still in view")
        self.assertEqual(len(speaker.said), 1)  # same answer: stay quiet
        triage.analyze = lambda jpeg, context: {**GOOD, "urgency": "medium", "spoken_alert": "changed"}
        clock.t += 31
        triage.poll_once()
        self.assertEqual(speaker.said[-1], "changed")

    def test_more_boxes_trigger_a_new_assessment(self):
        speaker = FakeSpeaker()
        camera = FakeCamera([person(0.1)])
        triage, clock = self.make(camera, speaker=speaker)
        triage.poll_once()
        camera.detections = [person(0.1), person(0.6)]
        clock.t += 9
        self.assertEqual(triage.poll_once(), "more people")
        self.assertIn("2 person box(es)", self.calls[-1])
        self.assertEqual(triage.status()["latest"]["detector_people"], 2)
        self.assertEqual(len(speaker.said), 1)  # Gemini still counts one unique person: stay quiet
        camera.detections = [person(0.1)]
        clock.t += 9
        self.assertIsNone(triage.poll_once())  # fewer boxes isn't a new sighting
        camera.detections = [person(0.1), person(0.6)]
        clock.t += 9
        self.assertIsNone(triage.poll_once())  # a box flickering back isn't either
        triage.analyze = lambda jpeg, context: {**GOOD, "unique_people": 3, "spoken_alert": "three"}
        camera.detections = [person(0.1), person(0.4), person(0.7)]
        clock.t += 9
        self.assertEqual(triage.poll_once(), "more people")
        self.assertEqual(speaker.said[-1], "three")

    def test_failed_call_falls_back_to_the_detector_alert(self):
        speaker = FakeSpeaker()
        triage, _ = self.make(FakeCamera([person(0.0)]), error=GeminiError("503"), speaker=speaker)
        triage.poll_once()
        self.assertEqual(speaker.said, ["Person detected on the left."])

    def test_status_reports_age_and_unique_count(self):
        triage, clock = self.make(FakeCamera([person()]), result={**GOOD, "unique_people": 1, "people_note": "boxes 1 and 2 are the same person"})
        triage.poll_once()
        clock.t += 5
        latest = triage.status()["latest"]
        self.assertEqual((latest["unique_people"], latest["age_s"]), (1, 5.0))
        self.assertIn("same person", latest["people_note"])

    def test_failure_is_reported_and_polling_continues(self):
        camera = FakeCamera([person()])
        triage, clock = self.make(camera, error=GeminiError("Gemini API error 429: quota"))
        triage.poll_once()
        self.assertEqual(triage.status()["state"], "error")
        self.assertIn("429", triage.status()["message"])
        triage, clock = self.make(camera, error=KeyError("boom"))
        triage.poll_once()
        self.assertIn("KeyError", triage.status()["message"])

    def test_saves_image_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            triage, _ = self.make(FakeCamera([person()]), save_dir=Path(tmp))
            triage.poll_once()
            files = sorted(p.suffix for p in Path(tmp).iterdir())
            self.assertEqual(files, [".jpg", ".json"])
            saved = json.loads(next(Path(tmp).glob("*.json")).read_text())
            self.assertEqual(saved["summary"], GOOD["summary"])
            self.assertEqual(saved["trigger"], "new person")
        self.assertEqual(triage.latest_jpeg(), JPEG)

    def test_unavailable_without_analyzer(self):
        triage = GeminiTriage(FakeCamera([person()]).status, lambda: JPEG, None, missing_reason="no key")
        triage.start()  # no thread without an analyzer
        self.assertFalse(triage.request())
        status = triage.status()
        self.assertEqual((status["state"], status["message"], status["can_ask"]), ("unavailable", "no key", False))
        triage.close()


class SessionCountTests(unittest.TestCase):
    def make(self, replies):
        self.contexts = []
        replies = iter(replies)

        def analyze(jpeg, context):
            self.contexts.append(context)
            return next(replies)

        clock = Clock()
        triage = GeminiTriage(FakeCamera([person()]).status, lambda: JPEG, analyze, clock=clock,
                              save_dir=None, min_gap_s=8, log=lambda m: None)
        return triage, clock

    def assess(self, triage, clock):
        clock.t += 10
        self.assertTrue(triage.request())
        triage.poll_once()

    def test_normalize_keeps_only_valid_people(self):
        out = normalize({**GOOD, "people": [
            {"appearance": " blue  coat ", "where": "left", "match": "new", "known_id": 7},
            {"appearance": "legs", "where": "under", "match": "maybe", "known_id": 1},
            {"appearance": "grey shirt", "where": "right", "match": "known", "known_id": 0},
            "not a person",
        ]})
        self.assertEqual(out["people"], [
            {"appearance": "blue coat", "where": "left", "match": "new", "known_id": 0},
            {"appearance": "legs", "where": "ahead", "match": "unclear", "known_id": 0},
            {"appearance": "grey shirt", "where": "right", "match": "unclear", "known_id": 0},
        ])
        self.assertEqual(normalize({"summary": "Empty room."})["people"], [])

    def test_total_counts_each_person_once_across_the_session(self):
        triage, clock = self.make([
            seen(("new", 0, "red hoodie", "left")),
            seen(("known", 1, "red hoodie, dark jeans", "ahead"), ("new", 0, "yellow vest", "right")),
            seen(("unclear", 0, "only legs visible", "ahead")),
            seen(("known", 9, "someone", "left")),  # unknown id: ignored, not counted
        ])
        for _ in range(4):
            self.assess(triage, clock)
        self.assertIn("No one has been seen yet", self.contexts[0])
        session = triage.status()["session"]
        self.assertEqual(session["total_unique"], 2)
        first, second = session["people"]
        self.assertEqual((first["id"], first["appearance"], first["sightings"]), (1, "red hoodie, dark jeans", 2))
        self.assertEqual((second["id"], second["appearance"], second["sightings"]), (2, "yellow vest", 1))
        # Later requests tell Gemini who has already been seen.
        self.assertIn("#1: red hoodie", self.contexts[1])
        self.assertIn("#2: yellow vest", self.contexts[3])
        self.assertEqual(triage.status()["latest"]["new_people"], [])

    def test_reset_starts_the_count_from_zero(self):
        triage, clock = self.make([seen(("new", 0, "red hoodie", "left")), seen(("new", 0, "green cap", "ahead"))])
        self.assess(triage, clock)
        self.assertEqual(triage.status()["session"]["total_unique"], 1)
        triage.reset_session()
        session = triage.status()["session"]
        self.assertEqual((session["total_unique"], session["people"]), (0, []))
        self.assess(triage, clock)
        self.assertNotIn("red hoodie", self.contexts[1])
        self.assertEqual([p["id"] for p in triage.status()["session"]["people"]], [1])

    def test_only_the_driving_browser_can_reset(self):
        triage = GeminiTriage(FakeCamera([person()]).status, lambda: JPEG, lambda j, c: dict(GOOD),
                              save_dir=None, log=lambda m: None)
        triage.poll_once()
        app = create_app(gemini_triage=triage)
        with TestClient(app) as client, \
                client.websocket_connect("/ws/control") as owner, client.websocket_connect("/ws/control") as viewer:
            owner.receive_json(), viewer.receive_json()
            owner.send_json({"type": "claim"})
            owner.receive_json(), owner.receive_json()
            viewer.send_json({"type": "gemini_reset_count"})
            self.assertEqual(viewer.receive_json(), {"type": "gemini_reset_count", "accepted": False})
            self.assertEqual(triage.status()["session"]["total_unique"], 1)
            owner.send_json({"type": "gemini_reset_count"})
            self.assertEqual(owner.receive_json(), {"type": "gemini_reset_count", "accepted": True})
            self.assertEqual(owner.receive_json()["data"]["gemini"]["session"]["total_unique"], 0)


class DashboardTests(unittest.TestCase):
    def test_gemini_off_by_default(self):
        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/state").json()["gemini"], {"enabled": False})
            self.assertEqual(client.get("/api/gemini/snapshot.jpg").status_code, 404)

    def test_flag_without_key_reports_why(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            app = create_app(camera=MockCameraBackend(), gemini=True)
        with TestClient(app) as client:
            state = client.get("/api/state").json()["gemini"]
        self.assertTrue(state["enabled"])
        self.assertEqual(state["state"], "unavailable")
        self.assertIn("GEMINI_API_KEY", state["message"])

    def test_flag_with_key_needs_live_camera(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            app = create_app(camera=MockCameraBackend(), gemini=True)
        self.assertIn("live camera", app.state.gemini.status()["message"])

    def test_assess_message_never_stops_the_robot(self):
        triage = GeminiTriage(FakeCamera([]).status, lambda: JPEG, lambda j, c: dict(GOOD),
                              save_dir=None, log=lambda m: None)
        app = create_app(gemini_triage=triage)
        with TestClient(app) as client, client.websocket_connect("/ws/control") as ws:
            ws.receive_json()
            ws.send_json({"type": "claim"})
            ws.receive_json(), ws.receive_json()
            with mock.patch.object(app.state.control_service, "stop") as stop:
                ws.send_json({"type": "gemini_assess"})
                self.assertEqual(ws.receive_json(), {"type": "gemini_assess", "accepted": True})
                state = ws.receive_json()["data"]
                stop.assert_not_called()
            self.assertTrue(state["gemini"]["enabled"])
            triage.poll_once()
            self.assertEqual(client.get("/api/state").json()["gemini"]["latest"]["urgency"], "high")
            image = client.get("/api/gemini/snapshot.jpg")
            self.assertEqual((image.status_code, image.content), (200, JPEG))

    def test_active_gemini_mutes_the_fixed_voice_alerts(self):
        from rescuebot.voice import CameraVoice
        voice = CameraVoice(FakeCamera([person()]).status, FakeSpeaker())
        active = GeminiTriage(FakeCamera().status, lambda: JPEG, lambda j, c: dict(GOOD), save_dir=None)
        create_app(camera_voice=voice, gemini_triage=active)
        self.assertTrue(voice.muted)
        self.assertIsNone(voice.poll_once())
        self.assertEqual(voice.speaker.said, [])

        voice = CameraVoice(FakeCamera([person()]).status, FakeSpeaker())
        inactive = GeminiTriage(FakeCamera().status, lambda: JPEG, None, missing_reason="no key")
        create_app(camera_voice=voice, gemini_triage=inactive)
        self.assertFalse(voice.muted)  # without a working Gemini the fixed alerts stay on
        self.assertEqual(voice.poll_once(), "Person detected ahead.")

    def test_assess_without_gemini_is_refused_but_harmless(self):
        app = create_app()
        with TestClient(app) as client, client.websocket_connect("/ws/control") as ws:
            ws.receive_json()
            with mock.patch.object(app.state.control_service, "stop") as stop:
                ws.send_json({"type": "gemini_assess"})
                self.assertEqual(ws.receive_json(), {"type": "gemini_assess", "accepted": False})
                stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
