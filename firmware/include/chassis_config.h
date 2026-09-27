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
    // Port mapping: M2=front-left, M4=front-right, M1=rear-left, M3=rear-right,
    // M1 and M3 inverted. "Front" is the camera end. The 2026-09-26 evening
    // per-port tests (after the motor wiring was reworked) recorded
    // M1=front-right, M2=rear-right, M3=front-left, M4=rear-left; on the floor
    // that drove forward and strafed correctly but rotated backwards, which is
    // exactly the diagonal swap below, so those tests had the ends reversed.
    // (The morning's floor-verified setting, before the rework, was M1 and M2
    // inverted.)
    WheelWiring front_left{2, false};
    WheelWiring front_right{4, false};
    WheelWiring rear_left{1, true};
    WheelWiring rear_right{3, true};

    // Maximum PWM (0-255) the hardware may be commanded to. 60 is a
    // raised-chassis bench value confirmed by the team on 2026-09-26, not a
    // validated floor-driving ceiling (IMPLEMENTATION_PLAN.md section 7); raise
    // it only after floor tests.
    int hardware_pwm_ceiling = 60;
};

}  // namespace rescuebot
