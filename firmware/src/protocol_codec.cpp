// Targets ArduinoJson v6.21 (see firmware/platformio.ini). This file has
// not been compiled in the development sandbox used to write it (no C++
// toolchain was available); see docs/handoffs/esp32-controller.md for what
// is and is not verified. Run `pio test -e native` first and fix any API
// mismatch before trusting this file.
#include "protocol_messages.h"

#include <ArduinoJson.h>

#include <cmath>
#include <cstring>

namespace rescuebot {

namespace {

// A flat drive packet (type/session/seq/forward/sideways/turn/speed_limit,
// 7 fields, plus a session string) does not fit in 256 bytes of
// ArduinoJson v6 StaticJsonDocument capacity once per-field node overhead
// and string duplication are accounted for — deserializeJson returns
// NoMemory and every valid drive packet gets rejected as malformed. 512
// gives comfortable headroom; this is independent of the wire-level line
// length bound (kMaxLineLength in line_reader.h caps input at 200 bytes
// before it ever reaches here).
constexpr size_t kJsonCapacity = 512;

bool copySessionField(JsonVariantConst v, char* dest) {
    if (!v.is<const char*>()) {
        return false;
    }
    const char* s = v.as<const char*>();
    if (s == nullptr) {
        return false;
    }
    size_t len = std::strlen(s);
    if (len == 0 || len >= kMaxSessionLength) {
        return false;
    }
    std::memcpy(dest, s, len + 1);
    return true;
}

bool readFiniteUnit(JsonVariantConst v, double* out) {
    if (!v.is<float>() && !v.is<int>()) {
        return false;
    }
    double d = v.as<double>();
    if (!std::isfinite(d) || d < -1.0 || d > 1.0) {
        return false;
    }
    *out = d;
    return true;
}

bool readSpeedLimit(JsonVariantConst v, int* out) {
    if (!v.is<int>()) {
        return false;
    }
    long value = v.as<long>();
    if (value < 0 || value > 255) {
        return false;
    }
    *out = static_cast<int>(value);
    return true;
}

bool readSeq(JsonVariantConst v, uint64_t* out) {
    if (!v.is<int>()) {
        return false;
    }
    long long value = v.as<long long>();
    if (value < 0) {
        return false;
    }
    *out = static_cast<uint64_t>(value);
    return true;
}

}  // namespace

InboundMessage parseInbound(const char* line, size_t length) {
    InboundMessage msg;
    if (line == nullptr || length == 0 || length >= kJsonCapacity) {
        msg.malformed = true;
        return msg;
    }

    StaticJsonDocument<kJsonCapacity> doc;
    DeserializationError err = deserializeJson(doc, line, length);
    if (err || !doc.is<JsonObject>()) {
        msg.malformed = true;
        return msg;
    }

    JsonVariantConst type_field = doc["type"];
    if (!type_field.is<const char*>()) {
        msg.malformed = true;
        return msg;
    }
    const char* type_str = type_field.as<const char*>();

    if (std::strcmp(type_str, "drive") == 0) {
        msg.type = InboundType::DRIVE;
        bool ok = copySessionField(doc["session"], msg.drive.session) &&
                  readSeq(doc["seq"], &msg.drive.seq) &&
                  readFiniteUnit(doc["forward"], &msg.drive.forward) &&
                  readFiniteUnit(doc["sideways"], &msg.drive.sideways) &&
                  readFiniteUnit(doc["turn"], &msg.drive.turn) &&
                  readSpeedLimit(doc["speed_limit"], &msg.drive.speed_limit);
        msg.malformed = !ok;
    } else if (std::strcmp(type_str, "arm") == 0) {
        msg.type = InboundType::ARM;
        bool ok = copySessionField(doc["session"], msg.arm_disarm.session) &&
                  readSeq(doc["seq"], &msg.arm_disarm.seq);
        msg.malformed = !ok;
    } else if (std::strcmp(type_str, "disarm") == 0) {
        msg.type = InboundType::DISARM;
        bool ok = copySessionField(doc["session"], msg.arm_disarm.session) &&
                  readSeq(doc["seq"], &msg.arm_disarm.seq);
        msg.malformed = !ok;
    } else {
        msg.malformed = true;
    }

    return msg;
}

size_t buildDriveAck(char* out, size_t out_size, const char* session, uint64_t ack_seq, int fl,
                      int fr, int rl, int rr) {
    StaticJsonDocument<kJsonCapacity> doc;
    doc["session"] = session;
    doc["ack"] = ack_seq;
    doc["fl"] = fl;
    doc["fr"] = fr;
    doc["rl"] = rl;
    doc["rr"] = rr;
    size_t n = serializeJson(doc, out, out_size);
    return n < out_size ? n : 0;
}

size_t buildArmAck(char* out, size_t out_size, const char* session, uint64_t seq, bool armed) {
    StaticJsonDocument<kJsonCapacity> doc;
    doc["type"] = "arm_ack";
    doc["session"] = session;
    doc["seq"] = seq;
    doc["armed"] = armed;
    size_t n = serializeJson(doc, out, out_size);
    return n < out_size ? n : 0;
}

size_t buildDisarmAck(char* out, size_t out_size, const char* session, uint64_t seq) {
    StaticJsonDocument<kJsonCapacity> doc;
    doc["type"] = "disarm_ack";
    doc["session"] = session;
    doc["seq"] = seq;
    doc["armed"] = false;
    size_t n = serializeJson(doc, out, out_size);
    return n < out_size ? n : 0;
}

size_t buildImuTelemetry(char* out, size_t out_size, uint32_t timestamp_ms, bool available,
                          double heading_deg, uint8_t calibration) {
    StaticJsonDocument<kJsonCapacity> doc;
    doc["type"] = "imu";
    doc["timestamp_ms"] = timestamp_ms;
    doc["available"] = available;
    if (available) {
        doc["heading"] = heading_deg;
        doc["calibration"] = calibration;
    }
    size_t n = serializeJson(doc, out, out_size);
    return n < out_size ? n : 0;
}

size_t buildFault(char* out, size_t out_size, const char* reason) {
    StaticJsonDocument<kJsonCapacity> doc;
    doc["type"] = "fault";
    doc["reason"] = reason;
    doc["armed"] = false;
    size_t n = serializeJson(doc, out, out_size);
    return n < out_size ? n : 0;
}

}  // namespace rescuebot
