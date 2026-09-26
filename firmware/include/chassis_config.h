#pragma once

#include <cstdint>

// Physical wheel mapping, direction inversion, and the hardware PWM ceiling.
//
// AGENTS.md: "Correct physical wheel direction through configuration, not
// equation edits." All wiring corrections belong here, never in mixing.cpp.

namespace rescuebot {

struct WheelWiring {
    uint8_t motor_shield_channel;  // Adafruit Motor Shield V2 port, 1-4
    bool inverted;                 // flips direction to correct wiring
};

struct ChassisConfig {
    // Verified 2026-09-26 on the raised chassis: one port at a time (FORWARD at
    // 60) gave M1=front-right, M2=rear-right, M3=front-left, M4=rear-left, and
    // mapped forward/backward/strafe/rotate checks all matched with the right
    // side (M1, M2) inverted. "Front" is the camera end.
    WheelWiring front_left{3, false};
    WheelWiring front_right{1, true};
    WheelWiring rear_left{4, false};
    WheelWiring rear_right{2, true};

    // Maximum PWM (0-255) the hardware may be commanded to. 60 is a
    // raised-chassis bench value confirmed by the team on 2026-09-26, not a
    // validated floor-driving ceiling (IMPLEMENTATION_PLAN.md section 7); raise
    // it only after floor tests.
    int hardware_pwm_ceiling = 60;
};

}  // namespace rescuebot
