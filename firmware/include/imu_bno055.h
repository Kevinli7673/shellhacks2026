#pragma once

#include <cstdint>

namespace rescuebot {

struct ImuReading {
    bool available = false;
    double heading_deg = 0.0;
    uint8_t calibration = 0;  // 0-3, BNO055 system calibration status
};

// Adapter over the Adafruit BNO055 (I2C). Must never block driving on sensor
// availability (IMPLEMENTATION_PLAN.md section 7: "Missing IMU data reports
// unavailable and does not block manual driving."). Only compiled for real
// firmware builds (guarded by ARDUINO in imu_bno055.cpp); native/test builds
// get a no-op stand-in.
class ImuSensor {
public:
    // One init attempt. Returns immediately if nothing answers at 0x28, but
    // once the sensor answers, Adafruit_BNO055::begin() takes ~1 s and waits
    // with no timeout for the chip to come back after its reset. Call it only
    // while disarmed; main.cpp's loop watchdog recovers from that wait.
    bool begin();

    ImuReading read();

    // Wire.endTransmission() result of the last probe at 0x28 (0 = answered,
    // 2 = no ACK), or 255 before the first probe. Diagnostic only.
    uint8_t lastProbe() const { return last_probe_; }

private:
    bool ready_ = false;
    uint8_t last_probe_ = 255;
};

}  // namespace rescuebot
