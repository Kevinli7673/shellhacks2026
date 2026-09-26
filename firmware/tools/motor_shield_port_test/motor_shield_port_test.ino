/*
 * Raised-chassis Adafruit Motor Shield V2 port test.
 *
 * Before uploading, replace the three placeholders below with the confirmed
 * ESP32-S2 I2C pins and motor-shield address. This sketch will refuse to run
 * while the pin placeholders remain unset. Keep the chassis raised and clear
 * of people and objects. It drives exactly one port at a time, FORWARD at PWM
 * 60 for two seconds, releases it, and prints the port number over Serial.
 *
 * Record, for M1 through M4: which physical wheel moved and the direction it
 * pushed. Do not change ChassisConfig channel or inversion settings until the
 * recorded results are available.
 */

#include <Wire.h>
#include <Adafruit_MotorShield.h>

constexpr int kI2cSdaPin = -1;  // Replace after confirming the ESP32-S2 wiring.
constexpr int kI2cSclPin = -1;  // Replace after confirming the ESP32-S2 wiring.
constexpr uint8_t kMotorShieldAddress = 0x00;  // Replace after confirming I2C address.

constexpr uint8_t kTestPwm = 60;
constexpr unsigned long kSpinDurationMs = 2000;
constexpr unsigned long kReleaseDurationMs = 1000;

Adafruit_MotorShield shield(kMotorShieldAddress);
Adafruit_DCMotor* motors[5] = {nullptr, nullptr, nullptr, nullptr, nullptr};
bool ready = false;

void releaseAll() {
  for (uint8_t port = 1; port <= 4; ++port) {
    if (motors[port] != nullptr) {
      motors[port]->setSpeed(0);
      motors[port]->run(RELEASE);
    }
  }
}

void setup() {
  Serial.begin(115200);
  const unsigned long wait_started_ms = millis();
  while (!Serial && millis() - wait_started_ms < 2000) {
  }

  if (kI2cSdaPin < 0 || kI2cSclPin < 0 || kMotorShieldAddress == 0x00) {
    Serial.println("Set confirmed I2C SDA, SCL, and shield address before testing.");
    return;
  }

  Wire.begin(kI2cSdaPin, kI2cSclPin);
  if (!shield.begin()) {
    Serial.println("Motor shield not found; check confirmed I2C wiring/address.");
    return;
  }

  for (uint8_t port = 1; port <= 4; ++port) {
    motors[port] = shield.getMotor(port);
  }
  releaseAll();
  ready = true;
  Serial.println("Raised-chassis port test ready.");
}

void loop() {
  if (!ready) {
    delay(1000);
    return;
  }

  for (uint8_t port = 1; port <= 4; ++port) {
    Serial.printf("Testing M%u: FORWARD, PWM %u, 2 seconds\n", port, kTestPwm);
    motors[port]->setSpeed(kTestPwm);
    motors[port]->run(FORWARD);
    delay(kSpinDurationMs);

    motors[port]->setSpeed(0);
    motors[port]->run(RELEASE);
    Serial.printf("M%u released\n", port);
    delay(kReleaseDurationMs);
  }

  Serial.println("Cycle complete; repeating in 3 seconds.");
  delay(3000);
}
