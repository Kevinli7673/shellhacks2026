#pragma once

#include <cstdint>

// Physical wheel mapping, direction inversion, and the validated hardware
// PWM ceiling.
//
// changes.md "Open hardware facts" lists these as NOT yet confirmed against
// real hardware: exact channel mapping, direction inversions, and validated
// motor-output ceiling. hardware_pwm_ceiling defaults to 0 so the firmware
// cannot command real motor output at all until it is set from a physically
// validated value (IMPLEMENTATION_PLAN.md section 7: "Keep real driving
// disabled until hardware configuration is validated." / "Do not assume the
// original document's approximate 80% PWM cap is universally safe.").
//
// AGENTS.md: "Correct physical wheel direction through configuration, not
// equation edits." All wiring corrections belong here, never in mixing.cpp.

namespace rescuebot {

struct WheelWiring {
    uint8_t motor_shield_channel;  // Adafruit Motor Shield V2 port, 1-4
    bool inverted;                 // flips direction to correct wiring
};

struct ChassisConfig {
    // Inverted: bench test 2026-09-26 (all four ports driven FORWARD) showed
    // only the front-left wheel pushing backward; its motor leads are reversed
    // relative to the other three. Port M1 for front-left is still the
    // placeholder mapping and must be confirmed with a one-port-at-a-time test.
    WheelWiring front_left{1, true};
    WheelWiring front_right{2, false};
    WheelWiring rear_left{3, false};
    WheelWiring rear_right{4, false};

    // Validated maximum PWM (0-255) the hardware may be commanded to. Zero
    // until confirmed by physical testing; see
    // docs/handoffs/esp32-controller.md "Hardware facts still required".
    int hardware_pwm_ceiling = 0;
};

}  // namespace rescuebot
