#pragma once

#include <cstddef>
#include <cstdint>

namespace rescuebot {

constexpr size_t kMaxSessionLength = 40;

enum class DriveRejection {
    ACCEPTED,
    NOT_ARMED,
    WRONG_SESSION,
    STALE_SEQUENCE,
};

// Tracks arm/disarm state, the active session, and the motion watchdog.
// Pure logic: no I/O, no Arduino dependency, fully host-testable (see
// firmware/test/test_session_guard).
//
// Safety invariants (IMPLEMENTATION_PLAN.md sections 5-7):
// - Boot, faults, watchdog expiry, and disarm leave driving disabled.
// - Reconnection alone, without an explicit arm(), never arms
//   ("Reconnection or a transport handshake must never arm the robot.").
// - Only strictly increasing sequence numbers within the currently armed
//   session are accepted; stale/duplicate/out-of-order/wrong-session drive
//   commands are ignored without disarming and without refreshing the
//   watchdog.
// - A stale arm (same session, seq not higher than the last one seen) is
//   ignored the same way; an arm for a different session is always
//   accepted, since a genuine reconnect starts a fresh session.
// - A malformed packet always disarms immediately; call faultDisarm() from
//   the caller once the protocol layer flags a packet malformed.
class SessionGuard {
public:
    explicit SessionGuard(uint32_t watchdog_timeout_ms = 500);

    // Boot state: disarmed, no session, watchdog clock idle.
    void reset();

    // Explicit arm request. Ignored (returns false, no state change) when
    // session matches the currently remembered session and seq is not
    // higher than the last one seen for it — a stale/replayed arm. Any
    // other arm (a new session, or a higher seq within the same session)
    // is accepted: establishes/refreshes the session, accepts seq as the
    // new baseline, resets the watchdog clock, and returns true. The
    // caller is responsible for zeroing motor outputs on an accepted arm
    // so nothing moves until the first new drive packet.
    bool arm(const char* session, uint64_t seq, uint32_t now_ms);

    // Explicit disarm/stop request. Always disarms immediately regardless
    // of session ("Stop overrides everything").
    void disarm();

    // A malformed packet (bad JSON, wrong types, non-finite, out-of-range,
    // oversized) forces an immediate disarm (IMPLEMENTATION_PLAN.md
    // section 6: "Malformed movement packets stop and disarm.").
    void faultDisarm();

    // Validates freshness/session/arm state for an already well-formed
    // drive command. On ACCEPTED, refreshes the watchdog clock and the
    // last-seen sequence number; the caller is responsible for computing
    // and writing motor outputs and must not do so otherwise.
    DriveRejection tryDrive(const char* session, uint64_t seq, uint32_t now_ms);

    // Call every loop iteration. Returns true the call where the watchdog
    // transitions armed -> disarmed due to timeout.
    bool checkWatchdog(uint32_t now_ms);

    bool armed() const { return armed_; }
    bool hasSession() const { return has_session_; }
    const char* session() const { return session_; }

    void setWatchdogTimeoutMs(uint32_t ms) { watchdog_timeout_ms_ = ms; }
    uint32_t watchdogTimeoutMs() const { return watchdog_timeout_ms_; }

private:
    bool sessionMatches(const char* session) const;

    bool armed_ = false;
    bool has_session_ = false;
    char session_[kMaxSessionLength] = {0};
    bool has_last_seq_ = false;
    uint64_t last_seq_ = 0;
    uint32_t last_valid_drive_ms_ = 0;
    uint32_t watchdog_timeout_ms_;
};

}  // namespace rescuebot
