// ESP32-S2 firmware entry point. Guarded by ARDUINO so native/test builds
// compile this to an empty translation unit (Unity's own main() runs
// instead). Not yet compiled or flashed (no board or toolchain available in
// the sandbox that wrote this). See docs/handoffs/esp32-controller.md.
//
// The protocol/safety decision logic itself lives in controller.h/.cpp,
// shared verbatim with the native test that replays
// firmware/test/fixtures/serial_protocol_vectors.json, so this file is
// just I/O plumbing: reading Serial, writing Serial, and turning
// Controller's outputs() into real motor commands via wiring.h.
#ifdef ARDUINO

#include <Arduino.h>
#include <Wire.h>

#include "chassis_config.h"
#include "controller.h"
#include "imu_bno055.h"
#include "line_reader.h"
#include "motor_shield.h"
#include "wiring.h"

namespace {

using rescuebot::Controller;
using rescuebot::ImuReading;
using rescuebot::ImuSensor;
using rescuebot::LineReader;
using rescuebot::MotorShield;

constexpr uint32_t kWatchdogTimeoutMs = 500;    // IMPLEMENTATION_PLAN.md section 7 default
constexpr uint32_t kImuIntervalMs = 50;         // ~20 Hz telemetry cadence
constexpr uint32_t kImuRetryIntervalMs = 1000;  // bounded, non-blocking re-init cadence
constexpr uint32_t kMotorRetryIntervalMs = 1000;  // bounded, non-blocking shield re-init

LineReader g_line_reader;
rescuebot::ChassisConfig g_chassis;  // TODO: set validated wheel mapping/ceiling before Stage G
Controller g_controller(kWatchdogTimeoutMs, g_chassis.hardware_pwm_ceiling);
MotorShield g_motors;
ImuSensor g_imu;

char g_out_buf[256];
bool g_imu_ready = false;
// The shield must be initialized before any write reaches it; without this,
// MotorShield holds no motor handles and every write is silently dropped.
bool g_motors_ready = false;
uint32_t g_last_motor_attempt_ms = 0;

// Measured on the QT Py ESP32-S2: after the libraries' own Wire.begin()
// calls, each I2C transaction took ~8.6 ms although getClock() reported
// 100 kHz, so one four-motor update stalled the loop ~0.5 s. Setting the
// clock explicitly reconfigures the bus (~0.06 ms per transaction at 400 kHz).
// The motor shield (PCA9685) and BNO055 both support 400 kHz. Reapply after
// every device begin(), since a begin() may reinitialize Wire. Adafruit_BNO055
// begin() leaves the bus slow (~2.2 ms per transaction) while getClock() still
// reports the old value, and setClock() skips an unchanged value, so set a
// different clock first to force a real reconfiguration.
constexpr uint32_t kI2cClockHz = 400000;
void configureI2cBus() {
    Wire.setClock(100000);
    Wire.setClock(kI2cClockHz);
}
uint32_t g_last_imu_ms = 0;
uint32_t g_last_imu_attempt_ms = 0;

void sendLine(const char* text, size_t len) {
    if (len == 0) {
        return;
    }
    Serial.write(reinterpret_cast<const uint8_t*>(text), len);
    Serial.write('\n');
}

// Applies wiring to the controller's current outputs and writes them, or
// forces everything off when disarmed. Called once per processed line and
// once per watchdog tick, matching how often outputs() can actually change.
void applyControllerOutputs() {
    if (!g_controller.armed()) {
        g_motors.allOff();
        return;
    }
    g_motors.write(rescuebot::applyChassisConfig(g_controller.outputs(), g_chassis));
}

}  // namespace

void setup() {
    Serial.begin(115200);
    // Initialize the shield first; begin() leaves every output off (section 7).
    g_motors_ready = g_motors.begin();
    configureI2cBus();
    g_last_motor_attempt_ms = millis();
    g_controller.setHardwarePwmCeiling(g_chassis.hardware_pwm_ceiling);

    // Lets the Pi notice a reboot immediately instead of only inferring it
    // once acks stop arriving (up to 250 ms later).
    size_t n = g_controller.boot(g_out_buf, sizeof(g_out_buf));
    sendLine(g_out_buf, n);

    g_imu_ready = g_imu.begin();
    configureI2cBus();
    g_last_imu_attempt_ms = millis();
}

void loop() {
    uint32_t now_ms = millis();

    while (Serial.available() > 0) {
        char c = static_cast<char>(Serial.read());
        if (g_line_reader.feed(c)) {
            size_t n = g_controller.handleLine(g_line_reader.line(), g_line_reader.length(),
                                                g_line_reader.overflowed(), now_ms, g_out_buf,
                                                sizeof(g_out_buf));
            sendLine(g_out_buf, n);
            applyControllerOutputs();
            g_line_reader.reset();
        }
    }

    {
        size_t n = g_controller.tick(now_ms, g_out_buf, sizeof(g_out_buf));
        if (n > 0) {
            sendLine(g_out_buf, n);
            applyControllerOutputs();
        }
    }

    if (!g_motors_ready && now_ms - g_last_motor_attempt_ms >= kMotorRetryIntervalMs) {
        g_motors_ready = g_motors.begin();
        configureI2cBus();
        g_last_motor_attempt_ms = now_ms;
    }

    if (!g_imu_ready && now_ms - g_last_imu_attempt_ms >= kImuRetryIntervalMs) {
        g_imu_ready = g_imu.begin();
        configureI2cBus();
        g_last_imu_attempt_ms = now_ms;
    }

    if (now_ms - g_last_imu_ms >= kImuIntervalMs) {
        g_last_imu_ms = now_ms;
        ImuReading reading = g_imu_ready ? g_imu.read() : ImuReading{};
        size_t n = rescuebot::buildImuTelemetry(g_out_buf, sizeof(g_out_buf), now_ms,
                                                 reading.available, reading.heading_deg,
                                                 reading.calibration);
        sendLine(g_out_buf, n);
    }
}

#endif  // ARDUINO
