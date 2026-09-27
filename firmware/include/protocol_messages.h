#pragma once

#include <cstddef>
#include <cstdint>

#include "session_guard.h"  // kMaxSessionLength

// Newline-delimited JSON wire protocol, IMPLEMENTATION_PLAN.md section 6.
//
// The drive packet shape (type/session/seq/forward/sideways/turn/
// speed_limit) and the drive ack shape (session/ack/fl/fr/rl/rr) are frozen
// shared interfaces per WORKSTREAMS.md and must not change without a
// recorded decision in changes.md and a synchronized update on both
// branches. The arm/disarm/fault/imu message shapes below are this
// workstream's own addition (arm/disarm *semantics* are frozen; their exact
// wire fields were not yet defined) and should be treated as a proposal to
// reconcile with the dashboard/control workstream's serial motor bridge
// before Stage G integration.

namespace rescuebot {

enum class InboundType { UNKNOWN, DRIVE, ARM, DISARM };

struct InboundDrive {
    char session[kMaxSessionLength] = {0};
    uint64_t seq = 0;
    double forward = 0.0;
    double sideways = 0.0;
    double turn = 0.0;
    int speed_limit = 0;
};

struct InboundArmDisarm {
    char session[kMaxSessionLength] = {0};
    uint64_t seq = 0;
};

struct InboundMessage {
    InboundType type = InboundType::UNKNOWN;
    // True for anything structurally invalid, wrong-typed, missing a
    // required field, non-finite, or out of range. Per
    // IMPLEMENTATION_PLAN.md section 6, the caller must treat this as a
    // malformed packet: never drive motors, never refresh the watchdog, and
    // disarm immediately.
    bool malformed = false;
    InboundDrive drive;
    InboundArmDisarm arm_disarm;
};

// Parses one bounded, newline-stripped line (length must not include the
// trailing '\n'). Oversized lines should be rejected by the caller (see
// LineReader::overflowed()) before reaching this function.
InboundMessage parseInbound(const char* line, size_t length);

// Outbound builders. Each writes a JSON object (no trailing newline) into
// `out` and returns the number of bytes written, or 0 if `out_size` was too
// small to hold the result.
size_t buildDriveAck(char* out, size_t out_size, const char* session, uint64_t ack_seq, int fl,
                      int fr, int rl, int rr);
size_t buildArmAck(char* out, size_t out_size, const char* session, uint64_t seq, bool armed);
size_t buildDisarmAck(char* out, size_t out_size, const char* session, uint64_t seq);
size_t buildImuTelemetry(char* out, size_t out_size, uint32_t timestamp_ms, bool available,
                          double heading_deg, uint8_t calibration);
size_t buildFault(char* out, size_t out_size, const char* reason);
// Diagnostic reports. The Pi bridge does not parse these (unknown types are
// counted as rejected lines and ignored).
// {"type":"status","motor_shield":true,"rx_dropped":0,"loop_max_ms":3}:
// shield presence, USB receive bytes dropped since boot, and the longest
// loop() pass since the previous status.
size_t buildStatus(char* out, size_t out_size, bool motor_shield, uint32_t rx_dropped,
                    uint32_t loop_max_ms);
// {"type":"rx_reject","len":N,"line":"..."}: the start of a line that was
// rejected as malformed or oversized, truncated to max_chars.
size_t buildRxReject(char* out, size_t out_size, const char* line, size_t length,
                      size_t max_chars);

}  // namespace rescuebot
