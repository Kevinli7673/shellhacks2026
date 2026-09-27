/*
 * Raised-chassis Adafruit Motor Shield V2 port test.
 *
 * Confirmed hardware: Adafruit QT Py ESP32-S2 default Wire (SDA=GPIO7,
 * SCL=GPIO6) and motor shield address 0x60. Keep the chassis raised and clear
 * of people and objects. It drives exactly one port at a time, FORWARD at PWM
 * 60 for two seconds, releases it, and prints the port number over Serial.
 *
 * Recorded mapping: M1=front-right, M2=rear-right, M3=front-left,
 * M4=rear-left. The right side is direction-inverted in ChassisConfig.
 */

#include <Wire.h>
#include <Adafruit_MotorShield.h>

constexpr int kI2cSdaPin = 7;
constexpr int kI2cSclPin = 6;
constexpr uint8_t kMotorShieldAddress = 0x60;

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
