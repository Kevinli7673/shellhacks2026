// Native host test for the newline-delimited JSON wire protocol. Run with:
//   pio test -e native -f test_protocol_codec
//
// protocol_codec.cpp targets the ArduinoJson v6.21 API from memory and has
// not been compiled anywhere yet (see docs/handoffs/esp32-controller.md).
// This suite is the first thing that must pass before trusting it.
#include <unity.h>

#include <ArduinoJson.h>

#include <cstring>

#include "protocol_messages.h"

using rescuebot::buildAccessoriesAck;
using rescuebot::buildArmAck;
using rescuebot::buildDisarmAck;
using rescuebot::buildDriveAck;
using rescuebot::buildFault;
using rescuebot::buildImuTelemetry;
using rescuebot::buildRxReject;
using rescuebot::buildStatus;
using rescuebot::InboundType;
using rescuebot::parseInbound;

void setUp(void) {}
void tearDown(void) {}

namespace {
size_t lineLen(const char* s) { return std::strlen(s); }
}  // namespace

// ---- Inbound parsing ----

void test_valid_drive_packet_parses(void) {
    const char* line =
        R"({"type":"drive","session":"abc123","seq":142,"forward":1.0,"sideways":0.0,"turn":0.0,"speed_limit":60})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_FALSE(msg.malformed);
    TEST_ASSERT_EQUAL(InboundType::DRIVE, msg.type);
    TEST_ASSERT_EQUAL_STRING("abc123", msg.drive.session);
    TEST_ASSERT_EQUAL_UINT64(142, msg.drive.seq);
    TEST_ASSERT_EQUAL_DOUBLE(1.0, msg.drive.forward);
    TEST_ASSERT_EQUAL_DOUBLE(0.0, msg.drive.sideways);
    TEST_ASSERT_EQUAL_DOUBLE(0.0, msg.drive.turn);
    TEST_ASSERT_EQUAL_INT(60, msg.drive.speed_limit);
}

void test_drive_packet_tolerates_unknown_extra_fields(void) {
    const char* line =
        R"({"type":"drive","session":"abc123","seq":1,"forward":0.0,"sideways":0.0,"turn":0.0,"speed_limit":10,"future_field":true})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_FALSE(msg.malformed);
}

void test_valid_arm_packet_parses(void) {
    const char* line = R"({"type":"arm","session":"abc123","seq":1})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_FALSE(msg.malformed);
    TEST_ASSERT_EQUAL(InboundType::ARM, msg.type);
    TEST_ASSERT_EQUAL_STRING("abc123", msg.arm_disarm.session);
    TEST_ASSERT_EQUAL_UINT64(1, msg.arm_disarm.seq);
}

void test_valid_disarm_packet_parses(void) {
    const char* line = R"({"type":"disarm","session":"abc123","seq":2})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_FALSE(msg.malformed);
    TEST_ASSERT_EQUAL(InboundType::DISARM, msg.type);
}

void test_valid_accessories_packet_parses(void) {
    const char* line =
        R"({"type":"accessories","session":"abc123","seq":3,"buzzer":true,"light":false})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_FALSE(msg.malformed);
    TEST_ASSERT_EQUAL(InboundType::ACCESSORIES, msg.type);
    TEST_ASSERT_EQUAL_STRING("abc123", msg.accessories.session);
    TEST_ASSERT_EQUAL_UINT64(3, msg.accessories.seq);
    TEST_ASSERT_TRUE(msg.accessories.buzzer);
    TEST_ASSERT_FALSE(msg.accessories.light);
}

void test_accessories_flags_must_be_booleans(void) {
    const char* numeric =
        R"({"type":"accessories","session":"abc123","seq":3,"buzzer":1,"light":false})";
    TEST_ASSERT_TRUE(parseInbound(numeric, lineLen(numeric)).malformed);
    const char* missing = R"({"type":"accessories","session":"abc123","seq":3,"buzzer":true})";
    TEST_ASSERT_TRUE(parseInbound(missing, lineLen(missing)).malformed);
}

void test_garbage_is_malformed(void) {
    const char* line = "not json at all";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_unknown_type_is_malformed(void) {
    const char* line = R"({"type":"teleport","session":"abc","seq":1})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_missing_required_field_is_malformed(void) {
    // Missing "turn".
    const char* line =
        R"({"type":"drive","session":"abc","seq":1,"forward":0.0,"sideways":0.0,"speed_limit":10})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_out_of_range_axis_is_malformed(void) {
    const char* line =
        R"({"type":"drive","session":"abc","seq":1,"forward":1.5,"sideways":0.0,"turn":0.0,"speed_limit":10})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_out_of_range_speed_limit_is_malformed(void) {
    const char* line =
        R"({"type":"drive","session":"abc","seq":1,"forward":0.0,"sideways":0.0,"turn":0.0,"speed_limit":256})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_negative_speed_limit_is_malformed(void) {
    const char* line =
        R"({"type":"drive","session":"abc","seq":1,"forward":0.0,"sideways":0.0,"turn":0.0,"speed_limit":-1})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_non_integer_speed_limit_is_malformed(void) {
    // speed_limit must be an integer PWM ceiling (IMPLEMENTATION_PLAN.md
    // section 6), not a fractional value.
    const char* line =
        R"({"type":"drive","session":"abc","seq":1,"forward":0.0,"sideways":0.0,"turn":0.0,"speed_limit":60.5})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_empty_session_is_malformed(void) {
    const char* line =
        R"({"type":"drive","session":"","seq":1,"forward":0.0,"sideways":0.0,"turn":0.0,"speed_limit":10})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_wrong_type_for_field_is_malformed(void) {
    // seq must be numeric, not a string.
    const char* line =
        R"({"type":"drive","session":"abc","seq":"1","forward":0.0,"sideways":0.0,"turn":0.0,"speed_limit":10})";
    auto msg = parseInbound(line, lineLen(line));
    TEST_ASSERT_TRUE(msg.malformed);
}

void test_oversized_line_is_malformed(void) {
    char big[512];
    std::memset(big, 'a', sizeof(big) - 1);
    big[sizeof(big) - 1] = '\0';
    auto msg = parseInbound(big, std::strlen(big));
    TEST_ASSERT_TRUE(msg.malformed);
}

// ---- Outbound building ----

void test_build_drive_ack_matches_frozen_shape(void) {
    char buf[128];
    size_t n = buildDriveAck(buf, sizeof(buf), "abc123", 142, 60, 60, -60, 60);
    TEST_ASSERT_TRUE(n > 0);
    TEST_ASSERT_EQUAL_STRING(R"({"session":"abc123","ack":142,"fl":60,"fr":60,"rl":-60,"rr":60})",
                              buf);
}

void test_build_arm_ack(void) {
    char buf[128];
    size_t n = buildArmAck(buf, sizeof(buf), "abc123", 1, true);
    TEST_ASSERT_TRUE(n > 0);
    TEST_ASSERT_EQUAL_STRING(R"({"type":"arm_ack","session":"abc123","seq":1,"armed":true})", buf);
}

void test_build_disarm_ack(void) {
    char buf[128];
    size_t n = buildDisarmAck(buf, sizeof(buf), "abc123", 2);
    TEST_ASSERT_TRUE(n > 0);
    TEST_ASSERT_EQUAL_STRING(R"({"type":"disarm_ack","session":"abc123","seq":2,"armed":false})",
                              buf);
}

void test_build_accessories_ack(void) {
    char buf[128];
    size_t n = buildAccessoriesAck(buf, sizeof(buf), "abc123", 7, false, true);
    TEST_ASSERT_TRUE(n > 0);
    TEST_ASSERT_EQUAL_STRING(
        R"({"type":"accessories_ack","session":"abc123","seq":7,"buzzer":false,"light":true})",
        buf);
}

void test_build_fault(void) {
    char buf[128];
    size_t n = buildFault(buf, sizeof(buf), "watchdog_expired");
    TEST_ASSERT_TRUE(n > 0);
    TEST_ASSERT_EQUAL_STRING(R"({"type":"fault","reason":"watchdog_expired","armed":false})", buf);
}

void test_build_status(void) {
    char buf[128];
    TEST_ASSERT_TRUE(buildStatus(buf, sizeof(buf), true, 0, 3) > 0);
    TEST_ASSERT_EQUAL_STRING(
        R"({"type":"status","motor_shield":true,"rx_dropped":0,"loop_max_ms":3})", buf);
    TEST_ASSERT_TRUE(buildStatus(buf, sizeof(buf), false, 42, 120) > 0);
    TEST_ASSERT_EQUAL_STRING(
        R"({"type":"status","motor_shield":false,"rx_dropped":42,"loop_max_ms":120})", buf);
}

void test_build_rx_reject_truncates_and_escapes(void) {
    char buf[128];
    const char line[] = R"({"type":"dri{"type":"drive")";
    TEST_ASSERT_TRUE(buildRxReject(buf, sizeof(buf), line, std::strlen(line), 12) > 0);
    TEST_ASSERT_EQUAL_STRING(R"({"type":"rx_reject","len":27,"line":"{\"type\":\"dri"})", buf);
}

void test_build_imu_telemetry_round_trips(void) {
    // Floating-point serialization formatting is not asserted exactly;
    // round-trip through the parser instead.
    char buf[128];
    size_t n = buildImuTelemetry(buf, sizeof(buf), 123456, true, 90.0, 3);
    TEST_ASSERT_TRUE(n > 0);

    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, buf, n);
    TEST_ASSERT_FALSE(err);
    TEST_ASSERT_EQUAL_STRING("imu", doc["type"].as<const char*>());
    TEST_ASSERT_EQUAL_UINT32(123456, doc["timestamp_ms"].as<uint32_t>());
    TEST_ASSERT_TRUE(doc["available"].as<bool>());
    TEST_ASSERT_EQUAL_DOUBLE(90.0, doc["heading"].as<double>());
    TEST_ASSERT_EQUAL_INT(3, doc["calibration"].as<int>());
}

void test_build_imu_telemetry_unavailable_omits_heading(void) {
    char buf[128];
    size_t n = buildImuTelemetry(buf, sizeof(buf), 1, false, 0.0, 0);
    TEST_ASSERT_TRUE(n > 0);

    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, buf, n);
    TEST_ASSERT_FALSE(err);
    TEST_ASSERT_FALSE(doc["available"].as<bool>());
    TEST_ASSERT_FALSE(doc.containsKey("heading"));
}

void test_build_fails_gracefully_on_too_small_buffer(void) {
    char buf[4];
    size_t n = buildDriveAck(buf, sizeof(buf), "abc123", 142, 60, 60, 60, 60);
    TEST_ASSERT_EQUAL_UINT(0, n);
}

int main(int argc, char** argv) {
    UNITY_BEGIN();
    RUN_TEST(test_valid_drive_packet_parses);
    RUN_TEST(test_drive_packet_tolerates_unknown_extra_fields);
    RUN_TEST(test_valid_arm_packet_parses);
    RUN_TEST(test_valid_disarm_packet_parses);
    RUN_TEST(test_valid_accessories_packet_parses);
    RUN_TEST(test_accessories_flags_must_be_booleans);
    RUN_TEST(test_garbage_is_malformed);
    RUN_TEST(test_unknown_type_is_malformed);
    RUN_TEST(test_missing_required_field_is_malformed);
    RUN_TEST(test_out_of_range_axis_is_malformed);
    RUN_TEST(test_out_of_range_speed_limit_is_malformed);
    RUN_TEST(test_negative_speed_limit_is_malformed);
    RUN_TEST(test_non_integer_speed_limit_is_malformed);
    RUN_TEST(test_empty_session_is_malformed);
    RUN_TEST(test_wrong_type_for_field_is_malformed);
    RUN_TEST(test_oversized_line_is_malformed);
    RUN_TEST(test_build_drive_ack_matches_frozen_shape);
    RUN_TEST(test_build_arm_ack);
    RUN_TEST(test_build_disarm_ack);
    RUN_TEST(test_build_accessories_ack);
    RUN_TEST(test_build_fault);
    RUN_TEST(test_build_status);
    RUN_TEST(test_build_rx_reject_truncates_and_escapes);
    RUN_TEST(test_build_imu_telemetry_round_trips);
    RUN_TEST(test_build_imu_telemetry_unavailable_omits_heading);
    RUN_TEST(test_build_fails_gracefully_on_too_small_buffer);
    return UNITY_END();
}
