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
#include <esp_attr.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include <cstring>

#include "accessory_config.h"
#include "accessory_outputs.h"
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
constexpr uint32_t kStatusIntervalMs = 1000;      // diagnostic status report cadence
// The USB CDC default is 256 bytes, about 0.1 s of drive packets at 20 Hz. When
// it fills the core silently drops bytes, so the next line arrives spliced and
// is rejected as malformed_packet. 4 KB covers any loop pass shorter than the
// 3 s loop watchdog.
constexpr size_t kSerialRxBufferBytes = 4096;
constexpr size_t kRxRejectMaxChars = 120;
// Resets the chip if setup() or one loop() pass blocks this long. The longest
// legitimate pass is a BNO055 begin() (~1.3 s). Adafruit_BNO055::begin() waits
// forever if the chip does not answer after its reset, which once left the
// robot silent (no IMU stream, no ACKs) until a manual RESET.
constexpr uint32_t kLoopWatchdogTimeoutS = 3;

LineReader g_line_reader;
rescuebot::ChassisConfig g_chassis;  // TODO: set validated wheel mapping/ceiling before Stage G
Controller g_controller(kWatchdogTimeoutMs, g_chassis.hardware_pwm_ceiling);
MotorShield g_motors;
ImuSensor g_imu;
rescuebot::AccessoryOutputs g_accessories;
// False after a watchdog reset, so a stuck BNO055 cannot reset the robot in a
// loop; the IMU then reports unavailable until the next power-on or RESET.
bool g_imu_enabled = true;

char g_out_buf[256];
bool g_imu_ready = false;

// Which step of loop() is running. Kept in RTC memory, which a watchdog reset
// does not clear, so after a reset imu_diag reports the step that hung
// (wdt_stage; 0 = not a watchdog reset). Diagnostic only.
enum Stage : uint32_t {
    kStageIdle = 1, kStageHandleLine, kStageReply, kStageMotorWrite, kStageAccessories,
    kStageRxReject, kStageTick, kStageMotorBegin, kStageImuBegin, kStageStatus,
    kStageImuRead, kStageImuSend, kStageSetup,
};
RTC_NOINIT_ATTR uint32_t g_stage;
uint32_t g_wdt_stage = 0;
inline void stage(Stage s) { g_stage = s; }
// The shield must be initialized before any write reaches it; without this,
// MotorShield holds no motor handles and every write is silently dropped.
bool g_motors_ready = false;
uint32_t g_last_motor_attempt_ms = 0;
uint32_t g_last_status_ms = 0;
// Written from the USB event task, read from loop().
volatile uint32_t g_rx_dropped = 0;
uint32_t g_loop_max_ms = 0;
char g_diag_buf[256];

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

// Why the IMU is unavailable: disabled after a watchdog reset (reset_reason
// 6), or not answering at 0x28 (probe != 0). No I2C traffic of its own.
void sendImuDiagnostics() {
    int n = snprintf(g_diag_buf, sizeof(g_diag_buf),
                     "{\"type\":\"imu_diag\",\"enabled\":%s,\"ready\":%s,\"probe_0x28\":%u,"
                     "\"reset_reason\":%d,\"wdt_stage\":%u}",
                     g_imu_enabled ? "true" : "false", g_imu_ready ? "true" : "false",
                     static_cast<unsigned>(g_imu.lastProbe()), static_cast<int>(esp_reset_reason()),
                     static_cast<unsigned>(g_wdt_stage));
    if (n > 0 && static_cast<size_t>(n) < sizeof(g_diag_buf)) {
        sendLine(g_diag_buf, static_cast<size_t>(n));
    }
}

void sendStatus() {
    size_t n = rescuebot::buildStatus(g_out_buf, sizeof(g_out_buf), g_motors_ready, g_rx_dropped,
                                      g_loop_max_ms);
    sendLine(g_out_buf, n);
    sendImuDiagnostics();
    g_loop_max_ms = 0;
}

void onRxOverflow(void*, esp_event_base_t, int32_t, void* event_data) {
    auto* data = static_cast<arduino_usb_cdc_event_data_t*>(event_data);
    g_rx_dropped += data->rx_overflow.dropped_bytes;
}

// Applies wiring to the controller's current outputs and writes them, or
// forces everything off when disarmed. Called once per processed line and
// once per watchdog tick, matching how often outputs() can actually change.
// Drive acks only echo the controller's math, so without this the Pi cannot
// tell whether the shield answered on I2C at all.
void applyControllerOutputs() {
    if (!g_controller.armed()) {
        g_motors.allOff();
        return;
    }
    g_motors.write(rescuebot::applyChassisConfig(g_controller.outputs(), g_chassis));
}

}  // namespace

void setup() {
    // Silence the buzzer and blank the light before anything slower runs.
    g_accessories.begin(rescuebot::AccessoryConfig{});
    esp_task_wdt_init(kLoopWatchdogTimeoutS, true);
    enableLoopWDT();
    g_imu_enabled = esp_reset_reason() != ESP_RST_TASK_WDT;
    g_wdt_stage = esp_reset_reason() == ESP_RST_TASK_WDT ? g_stage : 0;
    stage(kStageSetup);

    Serial.setRxBufferSize(kSerialRxBufferBytes);
    Serial.onEvent(ARDUINO_USB_CDC_RX_OVERFLOW_EVENT, onRxOverflow);
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
    sendStatus();
    g_last_status_ms = millis();

    if (g_imu_enabled) {
        g_imu_ready = g_imu.begin();
        configureI2cBus();
    }
    g_last_imu_attempt_ms = millis();
}

void loop() {
    uint32_t now_ms = millis();
    stage(kStageIdle);

    while (Serial.available() > 0) {
        char c = static_cast<char>(Serial.read());
        if (g_line_reader.feed(c)) {
            stage(kStageHandleLine);
            size_t n = g_controller.handleLine(g_line_reader.line(), g_line_reader.length(),
                                                g_line_reader.overflowed(), now_ms, g_out_buf,
                                                sizeof(g_out_buf));
            stage(kStageReply);
            sendLine(g_out_buf, n);
            stage(kStageMotorWrite);
            applyControllerOutputs();
            stage(kStageAccessories);
            g_accessories.apply(g_controller.accessories());
            if (n > 0 && (std::strstr(g_out_buf, "\"malformed_packet\"") != nullptr ||
                          std::strstr(g_out_buf, "\"oversized_packet\"") != nullptr)) {
                stage(kStageRxReject);
                size_t m = rescuebot::buildRxReject(g_diag_buf, sizeof(g_diag_buf),
                                                    g_line_reader.line(), g_line_reader.length(),
                                                    kRxRejectMaxChars);
                sendLine(g_diag_buf, m);
            }
            g_line_reader.reset();
        }
    }

    stage(kStageTick);
    {
        size_t n = g_controller.tick(now_ms, g_out_buf, sizeof(g_out_buf));
        if (n > 0) {
            sendLine(g_out_buf, n);
            applyControllerOutputs();
        }
    }

    stage(kStageMotorBegin);
    if (!g_motors_ready && now_ms - g_last_motor_attempt_ms >= kMotorRetryIntervalMs) {
        g_motors_ready = g_motors.begin();
        configureI2cBus();
        g_last_motor_attempt_ms = now_ms;
    }

    // Only while disarmed: a begin() that finds the sensor blocks ~1 s, longer
    // than the 500 ms drive watchdog.
    stage(kStageImuBegin);
    if (g_imu_enabled && !g_imu_ready && !g_controller.armed() &&
        now_ms - g_last_imu_attempt_ms >= kImuRetryIntervalMs) {
        g_imu_ready = g_imu.begin();
        configureI2cBus();
        g_last_imu_attempt_ms = now_ms;
    }

    stage(kStageStatus);
    if (now_ms - g_last_status_ms >= kStatusIntervalMs) {
        g_last_status_ms = now_ms;
        sendStatus();
    }

    if (now_ms - g_last_imu_ms >= kImuIntervalMs) {
        g_last_imu_ms = now_ms;
        stage(kStageImuRead);
        ImuReading reading = g_imu_ready ? g_imu.read() : ImuReading{};
        stage(kStageImuSend);
        size_t n = rescuebot::buildImuTelemetry(g_out_buf, sizeof(g_out_buf), now_ms,
                                                 reading.available, reading.heading_deg,
                                                 reading.calibration);
        sendLine(g_out_buf, n);
    }

    uint32_t pass_ms = millis() - now_ms;
    if (pass_ms > g_loop_max_ms) {
        g_loop_max_ms = pass_ms;
    }
}

#endif  // ARDUINO
