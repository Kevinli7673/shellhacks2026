#include "accessory_outputs.h"

#ifdef ARDUINO

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>

namespace rescuebot {

namespace {
AccessoryConfig g_config;
Adafruit_NeoPixel* g_jewel = nullptr;

// No other firmware code uses LEDC, so channel 0 is free.
constexpr uint8_t kBuzzerLedcChannel = 0;
constexpr uint8_t kBuzzerLedcBits = 8;
constexpr uint32_t kBuzzerDutyMax = (1u << kBuzzerLedcBits) - 1;

void writeBuzzer(bool on) {
    uint32_t idle = g_config.buzzer_active_high ? 0 : kBuzzerDutyMax;
    uint32_t sound = (kBuzzerDutyMax + 1) * g_config.buzzer_duty_percent / 100;
    if (!g_config.buzzer_active_high) {
        sound = kBuzzerDutyMax - sound;
    }
    ledcWrite(kBuzzerLedcChannel, on ? sound : idle);
}

void writeLight(bool on) {
    if (g_jewel == nullptr) {
        return;
    }
    if (on) {
        uint8_t level = g_config.light_level;
        g_jewel->fill(Adafruit_NeoPixel::Color(level, level, level, level));
    } else {
        g_jewel->clear();
    }
    g_jewel->show();
}
}  // namespace

void AccessoryOutputs::begin(const AccessoryConfig& config) {
    g_config = config;
    ledcSetup(kBuzzerLedcChannel, g_config.buzzer_tone_hz, kBuzzerLedcBits);
    ledcAttachPin(g_config.buzzer_pin, kBuzzerLedcChannel);
    writeBuzzer(false);
    static Adafruit_NeoPixel jewel(g_config.light_pixels, g_config.light_pin, NEO_GRBW + NEO_KHZ800);
    g_jewel = &jewel;
    g_jewel->begin();
    g_jewel->setBrightness(255);
    writeLight(false);
    applied_ = Accessories{};
    started_ = true;
}

void AccessoryOutputs::apply(const Accessories& wanted) {
    if (!started_) {
        return;
    }
    if (wanted.buzzer != applied_.buzzer) {
        writeBuzzer(wanted.buzzer);
        applied_.buzzer = wanted.buzzer;
    }
    if (wanted.light != applied_.light) {
        writeLight(wanted.light);
        applied_.light = wanted.light;
    }
}

}  // namespace rescuebot

#else  // !ARDUINO: native/test builds never touch real hardware.

namespace rescuebot {

void AccessoryOutputs::begin(const AccessoryConfig&) {}
void AccessoryOutputs::apply(const Accessories&) {}

}  // namespace rescuebot

#endif
