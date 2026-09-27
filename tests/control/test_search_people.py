from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).parents[2]
# search.py is plain Python; import it without a ROS install.
sys.path.insert(0, str(ROOT / "ros_ws" / "src" / "rescuebot_navigation"))

from rescuebot_navigation.search import MAX_REPORTED_PEOPLE, PeopleTracker  # noqa: E402

from rescuebot.navigation_ipc import decode_navigation_status, encode_navigation  # noqa: E402
from rescuebot.search_report import SearchReport  # noqa: E402


def navigation_record(people, now):
    return {
        "mission": "m1", "active": True, "goal_state": "executing", "ready": True, "reason": "Ready",
        "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "request_id": None, "expires_at": now + 0.5,
        "search": {"available": True, "phase": "exploring", "found": bool(people),
                   "home": {"x": 0.0, "y": 0.0, "yaw": 0.0}, "target": None, "visited": 3,
                   "reason": "Searching mapped free space", "people": people},
    }


class PeopleTrackerTests(unittest.TestCase):
    def test_one_person_seen_from_many_frames_counts_once(self):
        tracker = PeopleTracker()
        for i in range(20):
            tracker.add(2.0 + 0.05 * (i % 3), 1.0 - 0.04 * (i % 2), now=i * 0.1)
        self.assertEqual(tracker.count, 1)
        [[person_id, x, y, sightings, _age]] = tracker.report(2.0)
        self.assertEqual((person_id, sightings), (1, 20))
        self.assertAlmostEqual(x, 2.05, delta=0.05)
        self.assertAlmostEqual(y, 0.98, delta=0.05)

    def test_two_people_apart_count_twice_and_a_flicker_does_not_count(self):
        tracker = PeopleTracker()
        for i in range(6):
            tracker.add(1.0, 0.0, now=i)
            tracker.add(3.0, 2.0, now=i)
        tracker.add(-2.0, -2.0, now=7)  # one false box
        self.assertEqual(tracker.count, 2)
        self.assertEqual([p[0] for p in tracker.report(8)], [1, 2])

    def test_report_fits_the_status_datagram(self):
        tracker = PeopleTracker()
        for n in range(20):
            for i in range(5):
                tracker.add(n * 2.0, -n * 2.0, now=i)
        people = tracker.report(1000.0)
        self.assertEqual(len(people), MAX_REPORTED_PEOPLE)
        record = navigation_record(people, time.monotonic())
        self.assertLessEqual(len(encode_navigation(record)), 1024)
        self.assertEqual(decode_navigation_status(encode_navigation(record), time.monotonic())["search"]["people"], people)

    def test_status_without_people_is_still_accepted(self):
        record = navigation_record([], time.monotonic())
        del record["search"]["people"]
        decode_navigation_status(encode_navigation(record), time.monotonic())

    def test_malformed_people_are_rejected(self):
        now = time.monotonic()
        for bad in ([[1, 2.0, 3.0]], [["a", 1.0, 1.0, 5, 0.0]], [[0, 1.0, 1.0, 5, 0.0]], [[1, 1.0, 1.0, 5, "x"]]):
            with self.assertRaises(ValueError):
                decode_navigation_status(encode_navigation(navigation_record(bad, now)), now)


class SearchReportTests(unittest.TestCase):
    def gemini(self, time_text, age_s, summary, urgency="low"):
        return {"latest": {"time": time_text, "age_s": age_s, "latency_ms": 2000, "urgency": urgency,
                           "needs_help": urgency == "high", "posture": "sitting", "summary": summary}}

    def test_gemini_notes_attach_to_people_in_view_when_the_snapshot_was_taken(self):
        report = SearchReport()
        nav = {"mission": "m1", "search": {"phase": "exploring", "people": [[1, 1.0, 0.0, 9, 0.2], [2, 4.0, 1.0, 7, 30.0]]}}
        result = report.update(nav, self.gemini("T1", 0.5, "A person is sitting against the wall.", "high"))
        self.assertEqual(result["count"], 2)
        first, second = result["people"]
        self.assertEqual(first["gemini"]["summary"], "A person is sitting against the wall.")
        self.assertEqual(first["gemini"]["urgency"], "high")
        self.assertIsNone(second["gemini"])  # last seen 30 s ago, not in this snapshot

        # The note stays after the person leaves view; a new mission clears it.
        nav["search"]["people"][0][4] = 20.0
        self.assertIsNotNone(report.update(nav, self.gemini("T1", 20.0, "x"))["people"][0]["gemini"])
        fresh = report.update({"mission": "m2", "search": {"phase": "exploring", "people": [[1, 0.0, 0.0, 5, 50.0]]}},
                              self.gemini("T1", 50.0, "x"))
        self.assertIsNone(fresh["people"][0]["gemini"])


class PhysicalSearchReportInStateTests(unittest.TestCase):
    def test_dashboard_state_has_no_report_without_physical_autonomy(self):
        from rescuebot.web import _dashboard_state
        from rescuebot.replay_camera import MockCameraBackend
        from rescuebot.service import RobotControlService

        service = RobotControlService()
        self.addCleanup(service.close)
        state = _dashboard_state(service, None, MockCameraBackend(), report=SearchReport())
        self.assertNotIn("search_report", state)


if __name__ == "__main__":
    unittest.main()
