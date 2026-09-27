#pragma once

#include "accessory_config.h"
#include "controller.h"

namespace rescuebot {

// Drives the buzzer pin and the NeoPixel Jewel. Only compiled for real
// firmware builds (guarded by ARDUINO in accessory_outputs.cpp); native/test
// builds get a no-op stand-in.
class AccessoryOutputs {
public:
    // Call first in setup(): silences the buzzer and blanks the light.
    void begin(const AccessoryConfig& config);

    // Writes the hardware only when the requested state changed.
    void apply(const Accessories& wanted);

private:
    Accessories applied_;
    bool started_ = false;
};

}  // namespace rescuebot
