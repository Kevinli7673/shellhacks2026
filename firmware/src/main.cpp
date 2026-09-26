// ESP32-S2 firmware entry point. Guarded by ARDUINO so native/test builds
// compile this to an empty translation unit (Unity's own main() runs
// instead). Not yet compiled or flashed (no board or toolchain available in
// the sandbox that wrote this). See docs/handoffs/esp32-controller.md.
#ifdef ARDUINO

#include <Arduino.h>

#include "chassis_config.h"
#include "imu_bno055.h"
#include "line_reader.h"
#include "mixing.h"
#include "motor_shield.h"
#include "protocol_messages.h"
#include "session_guard.h"
#include "wiring.h"

namespace {

using rescuebot::ChassisConfig;
using rescuebot::DriveRejection;
using rescuebot::ImuReading;
using rescuebot::ImuSensor;
using rescuebot::InboundDrive;
using rescuebot::InboundMessage;
using rescuebot::InboundType;
using rescuebot::LineReader;
using rescuebot::MotorCommands;
using rescuebot::MotorShield;
using rescuebot::SessionGuard;
using rescuebot::WheelOutputs;

constexpr uint32_t kWatchdogTimeoutMs = 500;   // IMPLEMENTATION_PLAN.md section 7 default
constexpr uint32_t kImuIntervalMs = 50;        // ~20 Hz telemetry cadence
constexpr uint32_t kImuRetryIntervalMs = 1000;  // bounded, non-blocking re-init cadence

LineReader g_line_reader;
SessionGuard g_guard(kWatchdogTimeoutMs);
ChassisConfig g_chassis;  // TODO: set validated wheel mapping/ceiling before Stage G
MotorShield g_motors;
ImuSensor g_imu;

char g_out_buf[256];
bool g_imu_ready = false;
uint32_t g_last_imu_ms = 0;
uint32_t g_last_imu_attempt_ms = 0;

void sendLine(const char* text, size_t len) {
    if (len == 0) {
        return;
    }
    Serial.write(reinterpret_cast<const uint8_t*>(text), len);
    Serial.write('\n');
}

void handleDrive(const InboundDrive& drive, uint32_t now_ms) {
    DriveRejection result = g_guard.tryDrive(drive.session, drive.seq, now_ms);
    if (result != DriveRejection::ACCEPTED) {
        // Stale/duplicate/out-of-order/wrong-session/not-armed: ignore
        // silently. Do not drive motors, do not refresh the watchdog, do
        // not disarm (IMPLEMENTATION_PLAN.md section 6).
        return;
    }

    int limit = drive.speed_limit;
    if (limit > g_chassis.hardware_pwm_ceiling) {
        limit = g_chassis.hardware_pwm_ceiling;
    }

    WheelOutputs outputs = rescuebot::mix(drive.forward, drive.sideways, drive.turn, limit);
    MotorCommands commands = rescuebot::applyChassisConfig(outputs, g_chassis);
    g_motors.write(commands);

    size_t n = rescuebot::buildDriveAck(g_out_buf, sizeof(g_out_buf), drive.session, drive.seq,
                                         outputs.front_left, outputs.front_right,
                                         outputs.rear_left, outputs.rear_right);
    sendLine(g_out_buf, n);
}

void handleLine(const char* text, size_t len, uint32_t now_ms) {
    if (g_line_reader.overflowed()) {
        g_motors.allOff();
        g_guard.faultDisarm();
        size_t n = rescuebot::buildFault(g_out_buf, sizeof(g_out_buf), "oversized_packet");
        sendLine(g_out_buf, n);
        return;
    }

    InboundMessage msg = rescuebot::parseInbound(text, len);
    if (msg.malformed) {
        g_motors.allOff();
        g_guard.faultDisarm();
        size_t n = rescuebot::buildFault(g_out_buf, sizeof(g_out_buf), "malformed_packet");
        sendLine(g_out_buf, n);
        return;
    }

    switch (msg.type) {
        case InboundType::DRIVE:
            handleDrive(msg.drive, now_ms);
            break;
        case InboundType::ARM: {
            g_guard.arm(msg.arm_disarm.session, msg.arm_disarm.seq, now_ms);
            size_t n = rescuebot::buildArmAck(g_out_buf, sizeof(g_out_buf),
                                               msg.arm_disarm.session, msg.arm_disarm.seq, true);
            sendLine(g_out_buf, n);
            break;
        }
        case InboundType::DISARM: {
            g_motors.allOff();
            g_guard.disarm();
            size_t n = rescuebot::buildDisarmAck(g_out_buf, sizeof(g_out_buf),
                                                  msg.arm_disarm.session, msg.arm_disarm.seq);
            sendLine(g_out_buf, n);
            break;
        }
        case InboundType::UNKNOWN:
            // parseInbound flags genuinely unrecognized "type" values as
            // malformed above, so this case is unreachable in practice.
            break;
    }
}

}  // namespace

void setup() {
    Serial.begin(115200);
    g_motors.allOff();  // outputs off before sensor init (section 7)
    g_guard.reset();
    g_imu_ready = g_imu.begin();
    g_last_imu_attempt_ms = millis();
}

void loop() {
    uint32_t now_ms = millis();

    while (Serial.available() > 0) {
        char c = static_cast<char>(Serial.read());
        if (g_line_reader.feed(c)) {
            handleLine(g_line_reader.line(), g_line_reader.length(), now_ms);
            g_line_reader.reset();
        }
    }

    if (g_guard.checkWatchdog(now_ms)) {
        g_motors.allOff();
        size_t n = rescuebot::buildFault(g_out_buf, sizeof(g_out_buf), "watchdog_expired");
        sendLine(g_out_buf, n);
    }

    if (!g_imu_ready && now_ms - g_last_imu_attempt_ms >= kImuRetryIntervalMs) {
        g_imu_ready = g_imu.begin();
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
