import math
import unittest

from rescuebot.ros_conversion import RobotMotion, RosVelocity, robot_to_ros_velocity, ros_to_robot_motion


LIMITS = {"max_vx": 0.8, "max_vy": 0.6, "max_wz": 1.5}


class RosCoordinateConversionTests(unittest.TestCase):
    def test_ros_forward_remains_positive_robot_forward(self) -> None:
        motion = ros_to_robot_motion(0.8, 0.0, 0.0, **LIMITS)
        self.assertEqual(motion, RobotMotion(forward=1.0, sideways=0.0, turn=0.0))

    def test_ros_left_becomes_negative_robot_right_sideways(self) -> None:
        motion = ros_to_robot_motion(0.0, 0.3, 0.0, **LIMITS)
        self.assertEqual(motion, RobotMotion(forward=0.0, sideways=-0.5, turn=0.0))

    def test_ros_counterclockwise_becomes_negative_robot_clockwise_turn(self) -> None:
        motion = ros_to_robot_motion(0.0, 0.0, 0.75, **LIMITS)
        self.assertEqual(motion, RobotMotion(forward=0.0, sideways=0.0, turn=-0.5))

    def test_robot_right_and_clockwise_convert_back_to_ros_signs(self) -> None:
        velocity = robot_to_ros_velocity(0.5, 0.25, 0.5, **LIMITS)
        self.assertEqual(velocity, RosVelocity(linear_x=0.4, linear_y=-0.15, angular_z=-0.75))

    def test_conversion_clamps_ros_commands_before_the_host_mixer(self) -> None:
        self.assertEqual(
            ros_to_robot_motion(9.0, -9.0, 9.0, **LIMITS),
            RobotMotion(forward=1.0, sideways=1.0, turn=-1.0),
        )

    def test_nonfinite_values_and_invalid_limits_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ros_to_robot_motion(math.nan, 0.0, 0.0, **LIMITS)
        with self.assertRaises(ValueError):
            ros_to_robot_motion(0.0, 0.0, 0.0, max_vx=0.0, max_vy=0.6, max_wz=1.5)


if __name__ == "__main__":
    unittest.main()
