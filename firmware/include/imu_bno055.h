#pragma once

#include <cstdint>

namespace rescuebot {

struct ImuReading {
    bool available = false;
    double heading_deg = 0.0;
    uint8_t calibration = 0;  // 0-3, BNO055 system calibration status
};

// Adapter over the Adafruit BNO055 (I2C). A single bounded init attempt;
// never blocks driving on sensor availability (IMPLEMENTATION_PLAN.md
// section 7: "Missing IMU data reports unavailable and does not block
// manual driving."). Only compiled for real firmware builds (guarded by
// ARDUINO in imu_bno055.cpp); native/test builds get a no-op stand-in.
class ImuSensor {
public:
    // One bounded attempt. False on failure; the caller should retry later
    // at a low, timer-gated rate rather than looping here.
    bool begin();

    ImuReading read();

private:
    bool ready_ = false;
};

}  // namespace rescuebot
