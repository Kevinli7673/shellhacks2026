// Native host test for the pure mecanum mixing calculation. Run with:
//   pio test -e native -f test_mixing
//
// Expected values mirror firmware/test/fixtures/mixing_fixtures.json
// exactly (generated/verified via
// firmware/test/fixtures/generate_mixing_fixtures.py); keep both in sync
// if the fixtures change.
#include <unity.h>

#include "mixing.h"

using rescuebot::mix;
using rescuebot::WheelOutputs;

void setUp(void) {}
void tearDown(void) {}

namespace {

void assertOutputs(const WheelOutputs& out, int fl, int fr, int rl, int rr) {
    TEST_ASSERT_EQUAL_INT(fl, out.front_left);
    TEST_ASSERT_EQUAL_INT(fr, out.front_right);
    TEST_ASSERT_EQUAL_INT(rl, out.rear_left);
    TEST_ASSERT_EQUAL_INT(rr, out.rear_right);
}

}  // namespace

void test_zero_limit_is_all_zero(void) {
    assertOutputs(mix(1.0, 1.0, 1.0, 0), 0, 0, 0, 0);
}

void test_positive_forward_all_positive(void) {
    assertOutputs(mix(1.0, 0.0, 0.0, 100), 100, 100, 100, 100);
}

void test_negative_forward_all_negative(void) {
    assertOutputs(mix(-1.0, 0.0, 0.0, 100), -100, -100, -100, -100);
}

void test_positive_sideways_signs(void) {
    // FL/RR positive, FR/RL negative.
    assertOutputs(mix(0.0, 1.0, 0.0, 100), 100, -100, -100, 100);
}

void test_negative_sideways_signs(void) {
    assertOutputs(mix(0.0, -1.0, 0.0, 100), -100, 100, 100, -100);
}

void test_positive_turn_signs(void) {
    // FL/RL positive, FR/RR negative.
    assertOutputs(mix(0.0, 0.0, 1.0, 100), 100, -100, 100, -100);
}

void test_negative_turn_signs(void) {
    assertOutputs(mix(0.0, 0.0, -1.0, 100), -100, 100, -100, 100);
}

void test_diagonal_forward_right_normalizes_and_saturates(void) {
    // forward=1, sideways=1 exceeds unit magnitude; normalized to
    // 1/sqrt(2) each before scaling by the limit, then the raw
    // (141.42, 0, 0, 141.42) outputs are scaled back down together.
    assertOutputs(mix(1.0, 1.0, 0.0, 100), 100, 0, 0, 100);
}

void test_diagonal_forward_left_mirrors(void) {
    assertOutputs(mix(1.0, -1.0, 0.0, 100), 0, 100, 100, 0);
}

void test_forward_plus_turn_saturates(void) {
    // Raw outputs (200, 0, 200, 0) exceed the limit and are scaled by 0.5.
    assertOutputs(mix(1.0, 0.0, 1.0, 100), 100, 0, 100, 0);
}

void test_all_axes_combined(void) {
    // Raw outputs (180, -60, 60, 60) exceed the limit and are scaled by
    // 100/180 before rounding.
    assertOutputs(mix(0.6, 0.6, 0.6, 100), 100, -33, 33, 33);
}

void test_small_speed_limit_boundary(void) {
    assertOutputs(mix(1.0, 0.0, 0.0, 1), 1, 1, 1, 1);
}

void test_full_pwm_ceiling_boundary(void) {
    assertOutputs(mix(1.0, 0.0, 0.0, 255), 255, 255, 255, 255);
}

void test_fractional_turn_no_saturation(void) {
    assertOutputs(mix(0.0, 0.0, -0.5, 200), -100, 100, -100, 100);
}

void test_no_output_ever_exceeds_the_limit(void) {
    // Sweep a spread of inputs and confirm the saturation-scaling
    // invariant holds regardless of combination.
    const double axes[] = {-1.0, -0.5, -0.2, 0.0, 0.3, 0.7, 1.0};
    const int limits[] = {0, 1, 30, 100, 255};
    for (double f : axes) {
        for (double s : axes) {
            for (double t : axes) {
                for (int limit : limits) {
                    WheelOutputs out = mix(f, s, t, limit);
                    TEST_ASSERT_TRUE(out.front_left >= -limit && out.front_left <= limit);
                    TEST_ASSERT_TRUE(out.front_right >= -limit && out.front_right <= limit);
                    TEST_ASSERT_TRUE(out.rear_left >= -limit && out.rear_left <= limit);
                    TEST_ASSERT_TRUE(out.rear_right >= -limit && out.rear_right <= limit);
                }
            }
        }
    }
}

int main(int argc, char** argv) {
    UNITY_BEGIN();
    RUN_TEST(test_zero_limit_is_all_zero);
    RUN_TEST(test_positive_forward_all_positive);
    RUN_TEST(test_negative_forward_all_negative);
    RUN_TEST(test_positive_sideways_signs);
    RUN_TEST(test_negative_sideways_signs);
    RUN_TEST(test_positive_turn_signs);
    RUN_TEST(test_negative_turn_signs);
    RUN_TEST(test_diagonal_forward_right_normalizes_and_saturates);
    RUN_TEST(test_diagonal_forward_left_mirrors);
    RUN_TEST(test_forward_plus_turn_saturates);
    RUN_TEST(test_all_axes_combined);
    RUN_TEST(test_small_speed_limit_boundary);
    RUN_TEST(test_full_pwm_ceiling_boundary);
    RUN_TEST(test_fractional_turn_no_saturation);
    RUN_TEST(test_no_output_ever_exceeds_the_limit);
    return UNITY_END();
}
