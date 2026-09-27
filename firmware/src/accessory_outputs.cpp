#include "accessory_outputs.h"

#ifdef ARDUINO

#include <Adafruit_NeoPixel.h>
#include <Arduino.h>

namespace rescuebot {

namespace {
AccessoryConfig g_config;
Adafruit_NeoPixel* g_jewel = nullptr;

void writeBuzzer(bool on) {
    bool level = on == g_config.buzzer_active_high;
    digitalWrite(g_config.buzzer_pin, level ? HIGH : LOW);
}

void writeLight(bool on) {
    if (g_jewel == nullptr) {
        return;
    }
    if (on) {
        g_jewel->fill(Adafruit_NeoPixel::Color(255, 255, 255, 255));
    } else {
        g_jewel->clear();
    }
    g_jewel->show();
}
}  // namespace

void AccessoryOutputs::begin(const AccessoryConfig& config) {
    g_config = config;
    pinMode(g_config.buzzer_pin, OUTPUT);
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
