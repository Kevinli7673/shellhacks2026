// Native host test for arm/disarm/session/watchdog behavior. Run with:
//   pio test -e native -f test_session_guard
#include <unity.h>

#include "session_guard.h"

using rescuebot::DriveRejection;
using rescuebot::SessionGuard;

void setUp(void) {}
void tearDown(void) {}

void test_boot_state_is_disarmed(void) {
    SessionGuard guard(500);
    TEST_ASSERT_FALSE(guard.armed());
    TEST_ASSERT_EQUAL(DriveRejection::NOT_ARMED, guard.tryDrive("s1", 1, 0));
}

void test_arm_then_accepts_increasing_sequence(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    TEST_ASSERT_TRUE(guard.armed());
    TEST_ASSERT_EQUAL(DriveRejection::ACCEPTED, guard.tryDrive("s1", 11, 1010));
    TEST_ASSERT_EQUAL(DriveRejection::ACCEPTED, guard.tryDrive("s1", 12, 1020));
}

void test_stale_or_duplicate_sequence_is_ignored_without_disarming(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    guard.tryDrive("s1", 20, 1010);
    // Duplicate of the last accepted sequence.
    TEST_ASSERT_EQUAL(DriveRejection::STALE_SEQUENCE, guard.tryDrive("s1", 20, 1020));
    // Out-of-order (older than the last accepted sequence).
    TEST_ASSERT_EQUAL(DriveRejection::STALE_SEQUENCE, guard.tryDrive("s1", 15, 1030));
    // Still armed: a stale/duplicate packet must not disarm.
    TEST_ASSERT_TRUE(guard.armed());
}

void test_wrong_session_is_ignored_without_disarming(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    TEST_ASSERT_EQUAL(DriveRejection::WRONG_SESSION, guard.tryDrive("other", 999, 1010));
    TEST_ASSERT_TRUE(guard.armed());
}

void test_disarm_always_disarms(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    guard.disarm();
    TEST_ASSERT_FALSE(guard.armed());
    TEST_ASSERT_EQUAL(DriveRejection::NOT_ARMED, guard.tryDrive("s1", 11, 1010));
}

void test_fault_disarm_disarms_immediately(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    guard.faultDisarm();
    TEST_ASSERT_FALSE(guard.armed());
}

void test_reset_clears_session_and_arm_state(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    guard.reset();
    TEST_ASSERT_FALSE(guard.armed());
    TEST_ASSERT_FALSE(guard.hasSession());
    // A fresh arm() after reset must not be treated as a continuation of
    // the old session's sequence numbers.
    guard.arm("s2", 1, 2000);
    TEST_ASSERT_EQUAL(DriveRejection::ACCEPTED, guard.tryDrive("s2", 2, 2010));
}

void test_rearm_establishes_a_fresh_session_baseline(void) {
    SessionGuard guard(500);
    guard.arm("s1", 100, 1000);
    guard.tryDrive("s1", 101, 1010);
    guard.disarm();
    // Re-arming with a lower sequence in a new session must be accepted;
    // sequence numbers are only ordered within a session, not globally.
    guard.arm("s2", 1, 2000);
    TEST_ASSERT_EQUAL(DriveRejection::ACCEPTED, guard.tryDrive("s2", 2, 2010));
}

void test_stale_arm_same_session_equal_seq_is_ignored(void) {
    SessionGuard guard(500);
    TEST_ASSERT_TRUE(guard.arm("s1", 10, 1000));
    // Same session, seq not higher than the last one seen: ignored.
    TEST_ASSERT_FALSE(guard.arm("s1", 10, 2000));
    TEST_ASSERT_TRUE(guard.armed());
}

void test_stale_arm_same_session_lower_seq_is_ignored(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    guard.tryDrive("s1", 20, 1010);
    TEST_ASSERT_FALSE(guard.arm("s1", 15, 2000));
    TEST_ASSERT_TRUE(guard.armed());
}

void test_rearm_same_session_higher_seq_is_accepted(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    guard.tryDrive("s1", 11, 1010);
    TEST_ASSERT_TRUE(guard.arm("s1", 12, 2000));
    TEST_ASSERT_TRUE(guard.armed());
    // The new baseline is 12, not 11.
    TEST_ASSERT_EQUAL(DriveRejection::STALE_SEQUENCE, guard.tryDrive("s1", 12, 2010));
    TEST_ASSERT_EQUAL(DriveRejection::ACCEPTED, guard.tryDrive("s1", 13, 2020));
}

void test_arm_new_session_is_always_accepted_even_with_a_lower_seq(void) {
    // A genuine reconnect starts a fresh session; it must never be blocked
    // by the previous session's higher sequence numbers.
    SessionGuard guard(500);
    guard.arm("s1", 1000, 1000);
    TEST_ASSERT_TRUE(guard.arm("s2", 1, 2000));
    TEST_ASSERT_TRUE(guard.armed());
    TEST_ASSERT_EQUAL(DriveRejection::ACCEPTED, guard.tryDrive("s2", 2, 2010));
}

void test_watchdog_expires_and_disarms(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    // Just under the timeout: still armed.
    TEST_ASSERT_FALSE(guard.checkWatchdog(1499));
    TEST_ASSERT_TRUE(guard.armed());
    // Past the timeout: disarms and reports the transition once.
    TEST_ASSERT_TRUE(guard.checkWatchdog(1501));
    TEST_ASSERT_FALSE(guard.armed());
}

void test_watchdog_is_refreshed_by_accepted_drive_commands(void) {
    SessionGuard guard(500);
    guard.arm("s1", 10, 1000);
    guard.tryDrive("s1", 11, 1400);  // refreshes the watchdog clock
    TEST_ASSERT_FALSE(guard.checkWatchdog(1800));  // 400ms since refresh
    TEST_ASSERT_TRUE(guard.armed());
}

void test_watchdog_check_on_already_disarmed_guard_is_a_no_op(void) {
    SessionGuard guard(500);
    TEST_ASSERT_FALSE(guard.checkWatchdog(1000000));
}

int main(int argc, char** argv) {
    UNITY_BEGIN();
    RUN_TEST(test_boot_state_is_disarmed);
    RUN_TEST(test_arm_then_accepts_increasing_sequence);
    RUN_TEST(test_stale_or_duplicate_sequence_is_ignored_without_disarming);
    RUN_TEST(test_wrong_session_is_ignored_without_disarming);
    RUN_TEST(test_disarm_always_disarms);
    RUN_TEST(test_fault_disarm_disarms_immediately);
    RUN_TEST(test_reset_clears_session_and_arm_state);
    RUN_TEST(test_rearm_establishes_a_fresh_session_baseline);
    RUN_TEST(test_stale_arm_same_session_equal_seq_is_ignored);
    RUN_TEST(test_stale_arm_same_session_lower_seq_is_ignored);
    RUN_TEST(test_rearm_same_session_higher_seq_is_accepted);
    RUN_TEST(test_arm_new_session_is_always_accepted_even_with_a_lower_seq);
    RUN_TEST(test_watchdog_expires_and_disarms);
    RUN_TEST(test_watchdog_is_refreshed_by_accepted_drive_commands);
    RUN_TEST(test_watchdog_check_on_already_disarmed_guard_is_a_no_op);
    return UNITY_END();
}
