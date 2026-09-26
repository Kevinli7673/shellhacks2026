#pragma once

#include <cstdint>

#include "chassis_config.h"
#include "mixing.h"

// Applies wheel mapping/inversion to a pure mixing result. Kept separate
// from mixing.h/mixing.cpp so hardware corrections never touch the approved
// equations (AGENTS.md).

namespace rescuebot {

struct MotorCommand {
    uint8_t channel = 0;
    int signed_pwm = 0;  // negative = reverse direction on that channel
};

struct MotorCommands {
    MotorCommand front_left;
    MotorCommand front_right;
    MotorCommand rear_left;
    MotorCommand rear_right;
};

inline MotorCommand applyWiring(int logical_output, const WheelWiring& wiring) {
    MotorCommand command;
    command.channel = wiring.motor_shield_channel;
    command.signed_pwm = wiring.inverted ? -logical_output : logical_output;
    return command;
}

inline MotorCommands applyChassisConfig(const WheelOutputs& outputs, const ChassisConfig& config) {
    MotorCommands commands;
    commands.front_left = applyWiring(outputs.front_left, config.front_left);
    commands.front_right = applyWiring(outputs.front_right, config.front_right);
    commands.rear_left = applyWiring(outputs.rear_left, config.rear_left);
    commands.rear_right = applyWiring(outputs.rear_right, config.rear_right);
    return commands;
}

}  // namespace rescuebot
