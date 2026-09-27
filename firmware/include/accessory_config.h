#pragma once

#include <cstdint>

// Buzzer and light wiring on the Adafruit QT Py ESP32-S2, supplied by the team
// on 2026-09-26. Correct wiring here, never in the protocol or Controller.

namespace rescuebot {

struct AccessoryConfig {
    // Active buzzer module on A3. Sounds while its signal pin is driven.
    uint8_t buzzer_pin = 8;
    bool buzzer_active_high = true;

    // Adafruit NeoPixel Jewel 7, RGBW (SK6812), data in on RX. "On" is every
    // channel of every pixel at 255: up to ~0.5 A, so power the Jewel from 5V.
    uint8_t light_pin = 16;
    uint16_t light_pixels = 7;
};

}  // namespace rescuebot
