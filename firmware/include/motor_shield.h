#pragma once

#include <cstdint>

#include "wiring.h"

namespace rescuebot {

// Hardware adapter over the Adafruit Motor Shield V2 (I2C). Bounded,
// single-attempt operations only; never blocks or retries in a loop
// (IMPLEMENTATION_PLAN.md section 7). Only compiled for real firmware
// builds (guarded by ARDUINO in motor_shield.cpp); native/test builds get a
// no-op stand-in with the same interface.
class MotorShield {
public:
    // Must be called once in setup(), after Serial is up and before any
    // sensor init, leaving all outputs off (section 7: "Initialize motor
    // outputs off before sensor initialization.").
    bool begin();

    // Immediately disables all four motor outputs.
    void allOff();

    // Writes wiring-mapped commands. Rejects (returns false, outputs left
    // unchanged) if any signed_pwm is outside [-255, 255] as a defensive
    // bound; callers should already clamp via mixing + chassis_config.
    bool write(const MotorCommands& commands);

private:
    void writeChannel(uint8_t channel, int signed_pwm);
};

}  // namespace rescuebot
