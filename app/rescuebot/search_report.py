"""The search results panel: each person the room search counted, with Gemini's notes.

The ROS mission manager counts distinct people by map position (camera
bearing + LiDAR range). Gemini assesses camera snapshots on its own schedule.
Each new Gemini assessment is attached to every counted person the camera saw
around the time of that snapshot, so the report says who was found and how
each of them looked.
"""

from __future__ import annotations

from typing import Any


# A person counts as "in the snapshot" if the search saw them this long
# before the snapshot was taken (camera boxes flicker between frames).
SEEN_SLACK_S = 2.0


class SearchReport:
    def __init__(self) -> None:
        self._mission: str | None = None
        self._notes: dict[int, dict[str, Any]] = {}
        self._last_assessment: str | None = None

    def update(self, navigation: dict[str, Any] | None, gemini: dict[str, Any] | None) -> dict[str, Any]:
        navigation = navigation or {}
        search = navigation.get("search") or {}
        mission = navigation.get("mission")
        if mission is not None and mission != self._mission:
            # A new mission starts a new report; a finished one stays on screen.
            self._mission = mission
            self._notes = {}
        people = search.get("people") or []
        latest = (gemini or {}).get("latest")
        if latest and latest.get("time") != self._last_assessment:
            self._last_assessment = latest.get("time")
            # Seconds since the snapshot was taken: result age + Gemini's latency.
            snapshot_age = latest.get("age_s", 0) + latest.get("latency_ms", 0) / 1000
            note = {k: latest.get(k) for k in ("urgency", "needs_help", "posture", "summary", "time")}
            for person_id, _x, _y, _sightings, seen_ago in people:
                if seen_ago <= snapshot_age + SEEN_SLACK_S:
                    self._notes[person_id] = note
        return {
            "phase": search.get("phase", "idle"),
            "count": len(people),
            "people": [
                {"id": person_id, "x": x, "y": y, "sightings": sightings, "seen_s_ago": seen_ago,
                 "gemini": self._notes.get(person_id)}
                for person_id, x, y, sightings, seen_ago in people
            ],
        }
