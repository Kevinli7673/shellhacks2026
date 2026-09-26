// Adafruit BNO055 adapter. Written against the adafruit/Adafruit BNO055
// library API from memory; not yet compiled or run against real hardware
// (no board or toolchain available in the sandbox that wrote this). See
// docs/handoffs/esp32-controller.md.
#include "imu_bno055.h"

#ifdef ARDUINO

#include <Adafruit_BNO055.h>
#include <utility/imumaths.h>

namespace rescuebot {

namespace {
Adafruit_BNO055 g_bno(55, 0x28);
}  // namespace

bool ImuSensor::begin() {
    ready_ = g_bno.begin();
    return ready_;
}

ImuReading ImuSensor::read() {
    ImuReading reading;
    if (!ready_) {
        return reading;
    }
    sensors_event_t event;
    g_bno.getEvent(&event);
    reading.available = true;
    reading.heading_deg = event.orientation.x;
    uint8_t sys = 0, gyro = 0, accel = 0, mag = 0;
    g_bno.getCalibration(&sys, &gyro, &accel, &mag);
    reading.calibration = sys;
    return reading;
}

}  // namespace rescuebot

#else  // !ARDUINO: native/test builds never touch real hardware.

namespace rescuebot {

bool ImuSensor::begin() { return false; }
ImuReading ImuSensor::read() { return ImuReading{}; }

}  // namespace rescuebot

#endif
