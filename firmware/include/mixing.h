#pragma once

namespace rescuebot {

struct WheelOutputs {
    int front_left = 0;
    int front_right = 0;
    int rear_left = 0;
    int rear_right = 0;
};

// Pure mecanum mixing, exactly per IMPLEMENTATION_PLAN.md section 4. No
// Arduino or hardware dependency, so it can be exercised by native host
// tests (see firmware/tests/test_mixing).
//
// forward/sideways/turn are the commanded axes; callers must already have
// validated them as finite and within [-1, 1] before calling. speed_limit
// is the effective PWM ceiling in [0, 255], already bounded by the
// firmware's configured hardware ceiling (see chassis_config.h) — this
// function does not know about that ceiling.
//
// Wheel mapping and direction inversion are NOT applied here; see
// wiring.h / applyChassisConfig(). AGENTS.md: "Correct physical wheel
// direction through configuration, not equation edits."
WheelOutputs mix(double forward, double sideways, double turn, int speed_limit);

}  // namespace rescuebot
