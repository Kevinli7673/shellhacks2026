import math
import unittest

from rescuebot.mecanum import WheelOutputs, mix_mecanum


class MecanumMixingTests(unittest.TestCase):
    def test_positive_forward_matches_w_and_physical_forward_convention(self) -> None:
        wheels = mix_mecanum(forward=1, sideways=0, turn=0, speed_limit=100)
        self.assertEqual(wheels, WheelOutputs(100, 100, 100, 100))

    def test_positive_sideways_matches_d_and_physical_right_strafe_convention(self) -> None:
        wheels = mix_mecanum(forward=0, sideways=1, turn=0, speed_limit=100)
        self.assertEqual(wheels, WheelOutputs(100, -100, -100, 100))

    def test_positive_turn_matches_right_arrow_and_clockwise_convention(self) -> None:
        wheels = mix_mecanum(forward=0, sideways=0, turn=1, speed_limit=100)
        self.assertEqual(wheels, WheelOutputs(100, -100, 100, -100))

    def test_negative_axes_reverse_the_corresponding_logical_outputs(self) -> None:
        self.assertEqual(
            mix_mecanum(forward=-1, sideways=0, turn=0, speed_limit=80),
            WheelOutputs(-80, -80, -80, -80),
        )
        self.assertEqual(
            mix_mecanum(forward=0, sideways=-1, turn=0, speed_limit=80),
            WheelOutputs(-80, 80, 80, -80),
        )
        self.assertEqual(
            mix_mecanum(forward=0, sideways=0, turn=-1, speed_limit=80),
            WheelOutputs(-80, 80, -80, 80),
        )

    def test_translation_is_normalized_before_wheel_outputs(self) -> None:
        wheels = mix_mecanum(forward=1, sideways=1, turn=0, speed_limit=100)
        self.assertEqual(wheels, WheelOutputs(100, 0, 0, 100))

    def test_all_outputs_are_scaled_together_when_turn_and_translation_peak(self) -> None:
        wheels = mix_mecanum(forward=1, sideways=1, turn=1, speed_limit=100)
        self.assertEqual(wheels, WheelOutputs(100, -41, 41, 17))

    def test_zero_speed_limit_always_stops(self) -> None:
        self.assertEqual(
            mix_mecanum(forward=1, sideways=-1, turn=1, speed_limit=0),
            WheelOutputs.stopped(),
        )

    def test_rejects_invalid_inputs(self) -> None:
        for invalid in (math.inf, math.nan, 1.01, -1.01):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    mix_mecanum(invalid, 0, 0, 100)
        with self.assertRaises(ValueError):
            mix_mecanum(0, 0, 0, 100.0)  # type: ignore[arg-type]
