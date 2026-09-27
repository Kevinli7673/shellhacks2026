#pragma once

#include <cstdint>

// Buzzer and light wiring on the Adafruit QT Py ESP32-S2, supplied by the team
// on 2026-09-26. Correct wiring here, never in the protocol or Controller.

namespace rescuebot {

struct AccessoryConfig {
    // Buzzer module on A3. "On" is a square wave, so both active buzzers and
    // passive ones (which stay silent on a steady level) sound.
    uint8_t buzzer_pin = 8;
    bool buzzer_active_high = true;
    uint16_t buzzer_tone_hz = 2000;
    // Share of each cycle the pin is high (1-50). 50 is loudest; lower is quieter.
    uint8_t buzzer_duty_percent = 10;

    // Adafruit NeoPixel Jewel 7, RGBW (SK6812), data in on RX. Powered from the
    // QT Py's 3V pin so the 3.3 V data line reads reliably (at 5V it sat at the
    // SK6812's logic threshold and the Jewel stayed dark). "On" is every
    // channel of every pixel at light_level: 48 keeps it near 100 mA, inside
    // the 3.3 V regulator's headroom. Don't raise it much while on 3V.
    uint8_t light_pin = 16;
    uint16_t light_pixels = 7;
    uint8_t light_level = 48;
};

}  // namespace rescuebot
