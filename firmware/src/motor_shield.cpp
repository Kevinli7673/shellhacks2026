// Adafruit Motor Shield V2 adapter. Written against the
// adafruit/Adafruit Motor Shield V2 Library API from memory; not yet
// compiled or run against real hardware (no board or toolchain available in
// the sandbox that wrote this). See docs/handoffs/esp32-controller.md.
#include "motor_shield.h"

#ifdef ARDUINO

#include <Adafruit_MotorShield.h>

#include <algorithm>
#include <cstdlib>

namespace rescuebot {

namespace {
Adafruit_MotorShield g_shield;
Adafruit_DCMotor* g_motors[5] = {nullptr, nullptr, nullptr, nullptr, nullptr};
}  // namespace

bool MotorShield::begin() {
    if (!g_shield.begin()) {
        return false;
    }
    for (uint8_t ch = 1; ch <= 4; ++ch) {
        g_motors[ch] = g_shield.getMotor(ch);
    }
    allOff();
    return true;
}

void MotorShield::allOff() {
    for (uint8_t ch = 1; ch <= 4; ++ch) {
        if (g_motors[ch] != nullptr) {
            g_motors[ch]->run(RELEASE);
        }
    }
}

void MotorShield::writeChannel(uint8_t channel, int signed_pwm) {
    if (channel < 1 || channel > 4 || g_motors[channel] == nullptr) {
        return;
    }
    Adafruit_DCMotor* motor = g_motors[channel];
    if (signed_pwm == 0) {
        motor->run(RELEASE);
        motor->setSpeed(0);
        return;
    }
    motor->run(signed_pwm > 0 ? FORWARD : BACKWARD);
    motor->setSpeed(static_cast<uint8_t>(std::min(std::abs(signed_pwm), 255)));
}

bool MotorShield::write(const MotorCommands& commands) {
    const MotorCommand* all[4] = {&commands.front_left, &commands.front_right,
                                   &commands.rear_left, &commands.rear_right};
    for (const MotorCommand* cmd : all) {
        if (cmd->signed_pwm < -255 || cmd->signed_pwm > 255) {
            return false;
        }
    }
    for (const MotorCommand* cmd : all) {
        writeChannel(cmd->channel, cmd->signed_pwm);
    }
    return true;
}

}  // namespace rescuebot

#else  // !ARDUINO: native/test builds never touch real hardware.

namespace rescuebot {

bool MotorShield::begin() { return false; }
void MotorShield::allOff() {}
void MotorShield::writeChannel(uint8_t, int) {}
bool MotorShield::write(const MotorCommands&) { return false; }

}  // namespace rescuebot

#endif
