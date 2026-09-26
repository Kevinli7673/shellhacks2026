// Replays the dashboard/control workstream's shared serial protocol
// vectors (fixtures/serial_protocol_vectors.json on feature/serial-protocol,
// vendored here as firmware/test/fixtures/serial_protocol_vectors.json)
// against Controller, byte-for-byte the same way main.cpp feeds real serial
// bytes through LineReader. Run with:
//   pio test -e native -f test_protocol_fixtures
//
// This is the least-verified file in the firmware: it leans on ArduinoJson
// v6 object/array iteration APIs (JsonObjectConst, JsonArrayConst,
// JsonPairConst, containsKey, the `variant | default` idiom) that have not
// been compiled anywhere. Fix compile errors here first if this suite
// doesn't build.
//
// All cases are checked with plain comparisons collected into a failure
// list, and only ONE Unity assertion fires at the end (rather than
// TEST_ASSERT per field), so a mismatch in one case never hides results
// from the remaining ones the way Unity's abort-on-first-failure normally
// would inside a single test function.
//
// Known expected failure: the fixture's own description notes it matches
// firmware commit 48ae9dd and intentionally omits cases for behavior
// "still being agreed" (stale arm, extra/duplicate fields, boot message).
// But one already-committed case, "rearm_with_new_session_rejects_old_
// session", still asserts the PRE-fix "motors keep running through a
// re-arm" outputs (100/100/100/100 after the arm to a new session). Now
// that arm() zeroes outputs (see docs/handoffs/esp32-controller.md), that
// step's expected outputs are stale and this case is expected to fail
// until it's updated on their end — not edited here, since fixtures/ is
// the dashboard/control workstream's path.
#include <unity.h>

#include <ArduinoJson.h>

#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "controller.h"
#include "line_reader.h"

using rescuebot::Controller;
using rescuebot::LineReader;

void setUp(void) {}
void tearDown(void) {}

namespace {

std::string findFixturePath() {
    const char* filename = "serial_protocol_vectors.json";
    std::vector<std::string> candidates;
    if (const char* dir = std::getenv("RESCUEBOT_FIXTURE_DIR")) {
        candidates.push_back(std::string(dir) + "/" + filename);
    }
    // PlatformIO's native test working directory is not verified in this
    // sandbox (no toolchain available to run it); try the plausible ones.
    candidates.push_back(std::string("test/fixtures/") + filename);          // cwd = firmware/
    candidates.push_back(std::string("fixtures/") + filename);               // cwd = firmware/test/
    candidates.push_back(std::string("firmware/test/fixtures/") + filename); // cwd = repo root
    candidates.push_back(std::string("../fixtures/") + filename);            // cwd = .../test_protocol_fixtures/
    candidates.push_back(std::string("../../test/fixtures/") + filename);    // cwd = .pio/build/native/
    candidates.push_back(std::string("../../../test/fixtures/") + filename);

    for (const auto& candidate : candidates) {
        std::ifstream probe(candidate);
        if (probe.good()) {
            return candidate;
        }
    }
    return "";
}

bool jsonValueEquals(JsonVariantConst actual, JsonVariantConst expected) {
    if (expected.is<const char*>()) {
        return actual.is<const char*>() &&
               std::strcmp(actual.as<const char*>(), expected.as<const char*>()) == 0;
    }
    if (expected.is<bool>()) {
        return actual.is<bool>() && actual.as<bool>() == expected.as<bool>();
    }
    if (expected.is<double>() || expected.is<long long>()) {
        // Every numeric field in this protocol is an integer; compare as
        // long long to sidestep float rounding.
        return (actual.is<double>() || actual.is<long long>()) &&
               actual.as<long long>() == expected.as<long long>();
    }
    return false;
}

// Strict comparison: same key count, and every expected key present in
// actual with an equal value.
bool jsonObjectEquals(JsonObjectConst actual, JsonObjectConst expected) {
    size_t expected_count = 0;
    for (JsonPairConst kv : expected) {
        ++expected_count;
        const char* key = kv.key().c_str();
        if (!actual.containsKey(key) || !jsonValueEquals(actual[key], kv.value())) {
            return false;
        }
    }
    size_t actual_count = 0;
    for (JsonPairConst kv : actual) {
        (void)kv;
        ++actual_count;
    }
    return actual_count == expected_count;
}

// Runs one case, appending a human-readable message to `failures` for each
// mismatch found. Does not stop at the first mismatch within a case.
void runCase(JsonObjectConst test_case, uint32_t default_watchdog_ms, int default_ceiling,
             std::vector<std::string>& failures) {
    const char* case_name = test_case["name"] | "(unnamed case)";
    int ceiling = test_case["hardware_ceiling"] | default_ceiling;
    Controller controller(default_watchdog_ms, ceiling);
    LineReader reader;

    for (JsonObjectConst step : test_case["steps"].as<JsonArrayConst>()) {
        uint32_t t_ms = step["t_ms"] | 0;
        char out_buf[256];
        size_t n = 0;

        if (step.containsKey("send")) {
            const char* send_text = step["send"];
            reader.reset();
            for (const char* p = send_text; *p != '\0'; ++p) {
                reader.feed(*p);
            }
            reader.feed('\n');
            n = controller.handleLine(reader.line(), reader.length(), reader.overflowed(), t_ms,
                                       out_buf, sizeof(out_buf));
        } else if (step["tick"] | false) {
            n = controller.tick(t_ms, out_buf, sizeof(out_buf));
        }

        char context[128];
        std::snprintf(context, sizeof(context), "case '%s' step t_ms=%u", case_name,
                       static_cast<unsigned>(t_ms));

        JsonArrayConst expected_emit = step["emit"].as<JsonArrayConst>();
        if (expected_emit.size() == 0) {
            if (n != 0) {
                char msg[320];
                std::snprintf(msg, sizeof(msg), "%s: expected no emission, got %zu bytes: %.*s",
                              context, n, static_cast<int>(n), out_buf);
                failures.emplace_back(msg);
            }
        } else if (n == 0) {
            char msg[192];
            std::snprintf(msg, sizeof(msg), "%s: expected an emission but got none", context);
            failures.emplace_back(msg);
        } else {
            DynamicJsonDocument actual_doc(512);
            DeserializationError emit_err = deserializeJson(actual_doc, out_buf, n);
            if (emit_err) {
                char msg[320];
                std::snprintf(msg, sizeof(msg), "%s: emitted line was not valid JSON: %.*s",
                              context, static_cast<int>(n), out_buf);
                failures.emplace_back(msg);
            } else {
                JsonObjectConst expected_obj = expected_emit[0].as<JsonObjectConst>();
                if (!jsonObjectEquals(actual_doc.as<JsonObjectConst>(), expected_obj)) {
                    char msg[320];
                    std::snprintf(msg, sizeof(msg),
                                  "%s: emitted message did not match expected shape/values: got "
                                  "%.*s",
                                  context, static_cast<int>(n), out_buf);
                    failures.emplace_back(msg);
                }
            }
        }

        bool expected_armed = step["armed"] | false;
        if (expected_armed != controller.armed()) {
            char msg[192];
            std::snprintf(msg, sizeof(msg), "%s: armed state mismatch (expected %s, got %s)",
                           context, expected_armed ? "true" : "false",
                           controller.armed() ? "true" : "false");
            failures.emplace_back(msg);
        }

        JsonObjectConst expected_outputs = step["outputs"].as<JsonObjectConst>();
        const auto& outputs = controller.outputs();
        int expected_fl = expected_outputs["fl"].as<int>();
        int expected_fr = expected_outputs["fr"].as<int>();
        int expected_rl = expected_outputs["rl"].as<int>();
        int expected_rr = expected_outputs["rr"].as<int>();
        if (expected_fl != outputs.front_left || expected_fr != outputs.front_right ||
            expected_rl != outputs.rear_left || expected_rr != outputs.rear_right) {
            char msg[256];
            std::snprintf(msg, sizeof(msg),
                           "%s: wheel outputs mismatch (expected fl=%d fr=%d rl=%d rr=%d, got "
                           "fl=%d fr=%d rl=%d rr=%d)",
                           context, expected_fl, expected_fr, expected_rl, expected_rr,
                           outputs.front_left, outputs.front_right, outputs.rear_left,
                           outputs.rear_right);
            failures.emplace_back(msg);
        }
    }
}

}  // namespace

void test_shared_serial_protocol_vectors(void) {
    std::string path = findFixturePath();
    if (path.empty()) {
        TEST_FAIL_MESSAGE(
            "could not find serial_protocol_vectors.json under any candidate relative path; "
            "set RESCUEBOT_FIXTURE_DIR to its containing directory");
        return;
    }

    std::ifstream file(path);
    std::stringstream buffer;
    buffer << file.rdbuf();
    std::string content = buffer.str();

    // Generously sized for a host-only test; no embedded RAM constraint
    // applies here.
    DynamicJsonDocument doc(256 * 1024);
    DeserializationError err = deserializeJson(doc, content);
    if (err) {
        TEST_FAIL_MESSAGE("serial_protocol_vectors.json failed to parse as JSON");
        return;
    }

    uint32_t default_watchdog_ms = doc["watchdog_ms"] | 500;
    int default_ceiling = doc["hardware_ceiling"] | 0;

    std::vector<std::string> failures;
    size_t case_count = 0;
    for (JsonObjectConst test_case : doc["cases"].as<JsonArrayConst>()) {
        ++case_count;
        runCase(test_case, default_watchdog_ms, default_ceiling, failures);
    }

    if (!failures.empty()) {
        std::string report = std::to_string(failures.size()) + " mismatch(es) across " +
                              std::to_string(case_count) + " case(s):\n";
        for (const auto& f : failures) {
            report += "  - " + f + "\n";
        }
        TEST_FAIL_MESSAGE(report.c_str());
        return;
    }

    TEST_ASSERT_TRUE(case_count > 0);
}

int main(int argc, char** argv) {
    UNITY_BEGIN();
    RUN_TEST(test_shared_serial_protocol_vectors);
    return UNITY_END();
}
