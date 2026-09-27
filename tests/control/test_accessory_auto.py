from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import ai_camera_detect as cam  # noqa: E402

from rescuebot.accessory_auto import (  # noqa: E402
    ALERT_BUZZER_S,
    AccessoryAutomation,
    AutomationSnapshot,
    alert_outputs,
)
from rescuebot.control import ManualControl  # noqa: E402
from rescuebot.detections import DetectionFrame  # noqa: E402
from rescuebot.service import RobotControlService  # noqa: E402

from tests.control.test_bridge_backend import Rig  # noqa: E402


PERSON = {"label": "person", "confidence": 0.9, "bbox": {"x": 0.4, "y": 0.2, "width": 0.2, "height": 0.6}}


def camera(detections=(), lux=None, status="online") -> dict:
    frame = {"frame_id": 1} if lux is None else {"frame_id": 1, "lux": lux}
    return {"status": status, "detections": list(detections), "frame": frame}


class FakeCamera:
    def __init__(self) -> None:
        self.value = camera()

    def __call__(self) -> dict:
        return self.value


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeAutomation:
    def __init__(self) -> None:
        self.alert_seq = 0
        self.dark = False
        self.dark_seq = 0

    def alert(self) -> None:
        self.alert_seq += 1

    def set_dark(self, dark: bool) -> None:
        self.dark = dark
        self.dark_seq += 1

    def snapshot(self) -> AutomationSnapshot:
        return AutomationSnapshot(self.alert_seq, self.dark, self.dark_seq, None)


class AlertTimelineTests(unittest.TestCase):
    def test_buzzer_sounds_two_seconds_while_the_light_flashes_five_times(self) -> None:
        samples = [alert_outputs(i * 0.05) for i in range(40)]  # 0.00 .. 1.95 s
        self.assertTrue(all(sample is not None and sample[0] for sample in samples))
        lights = [sample[1] for sample in samples]
        rising = sum(1 for before, after in zip([False] + lights, lights) if after and not before)
        self.assertEqual(rising, 5)
        self.assertIsNone(alert_outputs(ALERT_BUZZER_S))
        self.assertIsNone(alert_outputs(-0.1))


class AccessoryAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.camera = FakeCamera()
        self.clock = FakeClock()
        self.auto = AccessoryAutomation(self.camera, hold_s=5.0, gone_after_s=3.0,
                                        clock=self.clock, log=lambda _: None)

    def poll(self, seconds: float = 0.0, **camera_kwargs) -> AutomationSnapshot:
        self.camera.value = camera(**camera_kwargs)
        self.clock.now += seconds
        self.auto.poll_once()
        return self.auto.snapshot()

    def test_alerts_once_per_arrival(self) -> None:
        self.assertEqual(self.poll(detections=[PERSON]).alert_seq, 1)
        self.assertEqual(self.poll(1.0, detections=[PERSON]).alert_seq, 1)
        self.assertEqual(self.poll(1.0).alert_seq, 1)  # a brief dropout is not a new arrival
        self.assertEqual(self.poll(1.0, detections=[PERSON]).alert_seq, 1)
        self.poll(1.0)
        self.poll(3.0)
        self.assertEqual(self.poll(0.5, detections=[PERSON]).alert_seq, 2)

    def test_ignores_other_labels_and_stale_camera(self) -> None:
        self.assertEqual(self.poll(detections=[{**PERSON, "label": "dog"}]).alert_seq, 0)
        self.assertEqual(self.poll(1.0, detections=[PERSON], status="stale").alert_seq, 0)

    def test_dark_needs_a_steady_reading_and_has_hysteresis(self) -> None:
        self.assertFalse(self.poll(lux=3.0).dark)
        self.assertFalse(self.poll(4.0, lux=3.0).dark)
        self.assertTrue(self.poll(1.0, lux=3.0).dark)
        self.assertTrue(self.poll(10.0, lux=25.0).dark)  # between thresholds: stays dark
        self.assertTrue(self.poll(1.0, lux=60.0).dark)
        self.assertTrue(self.poll(2.0, lux=5.0).dark)  # interrupted: the hold restarts
        self.assertTrue(self.poll(1.0, lux=60.0).dark)
        snap = self.poll(5.0, lux=60.0)
        self.assertFalse(snap.dark)
        self.assertEqual(snap.dark_seq, 2)

    def test_missing_readings_keep_the_current_state(self) -> None:
        self.poll(lux=1.0)
        self.assertTrue(self.poll(5.0, lux=1.0).dark)
        self.assertTrue(self.poll(30.0, status="offline").dark)
        self.assertTrue(self.poll(30.0).dark)  # online but no lux
        self.assertIsNone(self.auto.snapshot().lux)

    def test_rejects_thresholds_in_the_wrong_order(self) -> None:
        with self.assertRaises(ValueError):
            AccessoryAutomation(self.camera, dark_lux=50.0, bright_lux=10.0)


class ServiceAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auto = FakeAutomation()
        self.service = RobotControlService(ManualControl(pwm_ceiling=100), automation=self.auto)
        self.assertTrue(self.service.claim("browser-one"))

    def at(self, now: float) -> dict:
        self.service.tick(now)
        return dict(self.service.backend.accessories)

    def test_alert_plays_over_the_base_state_then_restores_it(self) -> None:
        self.service.set_accessory("browser-one", "light", True, now=1.0)
        self.auto.alert()
        self.assertEqual(self.at(2.0), {"buzzer": True, "light": True})
        self.assertEqual(self.at(2.3), {"buzzer": True, "light": False})
        self.assertEqual(self.at(3.9), {"buzzer": True, "light": False})
        self.assertEqual(self.at(4.0), {"buzzer": False, "light": True})
        self.assertFalse(self.service.state(now=4.1)["accessory_auto"]["alert_playing"])

    def test_buzzer_button_off_ends_the_alert(self) -> None:
        self.auto.alert()
        self.assertEqual(self.at(1.0), {"buzzer": True, "light": True})
        self.assertTrue(self.service.set_accessory("browser-one", "buzzer", False, now=1.5))
        self.assertEqual(self.at(1.6), {"buzzer": False, "light": False})

    def test_dark_acts_like_a_press_and_a_press_overrides_until_the_next_change(self) -> None:
        self.auto.set_dark(True)
        self.assertEqual(self.at(1.0), {"buzzer": False, "light": True})
        self.service.set_accessory("browser-one", "light", False, now=2.0)
        self.assertEqual(self.at(3.0), {"buzzer": False, "light": False})
        self.auto.set_dark(False)
        self.auto.set_dark(True)
        self.assertEqual(self.at(4.0), {"buzzer": False, "light": True})

    def test_owner_disconnect_clears_switches_but_keeps_the_dark_light(self) -> None:
        self.service.set_accessory("browser-one", "buzzer", True, now=1.0)
        self.auto.set_dark(True)
        self.assertEqual(self.at(1.1), {"buzzer": True, "light": True})
        self.service.disconnect("browser-one", now=2.0)
        self.assertEqual(self.at(2.1), {"buzzer": False, "light": True})

    def test_runs_without_an_owner_and_never_arms(self) -> None:
        self.service.disconnect("browser-one", now=0.5)
        self.auto.alert()
        self.assertEqual(self.at(1.0), {"buzzer": True, "light": True})
        self.assertFalse(self.service.control.armed)


class BridgeAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rig = Rig(self)
        self.auto = FakeAutomation()
        self.rig.service.automation = self.auto

    def test_alert_reaches_the_firmware_and_ends_off(self) -> None:
        self.rig.pump(3)
        self.auto.alert()
        self.rig.pump(5)
        self.assertEqual(self.rig.firmware.accessories, {"buzzer": True, "light": True})
        self.rig.pump(25)  # 0.25 s later: first flash off, buzzer still on
        self.assertEqual(self.rig.firmware.accessories, {"buzzer": True, "light": False})
        self.rig.pump(200)
        self.assertEqual(self.rig.firmware.accessories, {"buzzer": False, "light": False})
        self.assertFalse(self.rig.firmware.armed)

    def test_follows_the_firmware_after_it_switches_accessories_off(self) -> None:
        self.rig.pump(3)
        self.auto.set_dark(True)
        self.rig.pump(3)
        self.assertEqual(self.rig.firmware.accessories, {"buzzer": False, "light": True})
        self.rig.bridge.transport._pending.extend(self.rig.firmware.reboot()[0])
        self.rig.pump(150)
        self.assertEqual(self.rig.state()["motor"]["accessories"]["light"], False)
        # The next press works from the firmware's state.
        self.assertTrue(self.rig.service.set_accessory(self.rig.session, "light", True, now=self.rig.clock.now))
        self.rig.pump(3)
        self.assertEqual(self.rig.firmware.accessories, {"buzzer": False, "light": True})


class LuxRecordTests(unittest.TestCase):
    def test_camera_events_carry_lux_only_when_known(self) -> None:
        self.assertNotIn("lux", cam.make_event(1, "front", 640, 480, None, []))
        event = cam.make_event(1, "front", 640, 480, None, [], lux=12.345)
        self.assertEqual(event["lux"], 12.3)
        frame = DetectionFrame.from_dict(event)
        self.assertEqual(frame.lux, 12.3)
        self.assertEqual(frame.as_dict()["lux"], 12.3)
        self.assertEqual(frame.filtered().lux, 12.3)

    def test_rejects_bad_lux(self) -> None:
        base = cam.make_event(1, "front", 640, 480, None, [])
        for bad in (-1, "dark", float("nan")):
            with self.assertRaises(ValueError):
                DetectionFrame.from_dict({**base, "lux": bad})


if __name__ == "__main__":
    unittest.main()
