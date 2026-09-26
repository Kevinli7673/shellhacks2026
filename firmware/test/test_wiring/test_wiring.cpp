// Native regression test for physical wiring corrections. Run with:
//   pio test -e native -f test_wiring
//
// This verifies configuration after the pure mecanum calculation. Do not
// change the approved mixing equations to compensate for motor-lead wiring.
#include <unity.h>

#include "chassis_config.h"
#include "mixing.h"
#include "wiring.h"

using rescuebot::applyChassisConfig;
using rescuebot::ChassisConfig;
using rescuebot::mix;

void setUp(void) {}
void tearDown(void) {}

void test_confirmed_wiring_maps_forward_outputs_to_shield_ports(void) {
    const ChassisConfig config;
    const auto logical_outputs = mix(1.0, 0.0, 0.0, 100);
    const auto motor_commands = applyChassisConfig(logical_outputs, config);

    TEST_ASSERT_EQUAL_UINT8(3, motor_commands.front_left.channel);
    TEST_ASSERT_EQUAL_INT(100, motor_commands.front_left.signed_pwm);

    TEST_ASSERT_EQUAL_UINT8(1, motor_commands.front_right.channel);
    TEST_ASSERT_EQUAL_INT(-100, motor_commands.front_right.signed_pwm);

    TEST_ASSERT_EQUAL_UINT8(4, motor_commands.rear_left.channel);
    TEST_ASSERT_EQUAL_INT(100, motor_commands.rear_left.signed_pwm);

    TEST_ASSERT_EQUAL_UINT8(2, motor_commands.rear_right.channel);
    TEST_ASSERT_EQUAL_INT(-100, motor_commands.rear_right.signed_pwm);
}

int main(int argc, char** argv) {
    UNITY_BEGIN();
    RUN_TEST(test_confirmed_wiring_maps_forward_outputs_to_shield_ports);
    return UNITY_END();
}
