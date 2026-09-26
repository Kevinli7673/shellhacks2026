#include "session_guard.h"

#include <cstring>

namespace rescuebot {

SessionGuard::SessionGuard(uint32_t watchdog_timeout_ms)
    : watchdog_timeout_ms_(watchdog_timeout_ms) {}

void SessionGuard::reset() {
    armed_ = false;
    has_session_ = false;
    session_[0] = '\0';
    has_last_seq_ = false;
    last_seq_ = 0;
    last_valid_drive_ms_ = 0;
}

bool SessionGuard::sessionMatches(const char* session) const {
    return has_session_ && std::strncmp(session_, session, kMaxSessionLength) == 0;
}

void SessionGuard::arm(const char* session, uint64_t seq, uint32_t now_ms) {
    std::strncpy(session_, session, kMaxSessionLength - 1);
    session_[kMaxSessionLength - 1] = '\0';
    has_session_ = true;
    last_seq_ = seq;
    has_last_seq_ = true;
    armed_ = true;
    last_valid_drive_ms_ = now_ms;
}

void SessionGuard::disarm() {
    armed_ = false;
}

void SessionGuard::faultDisarm() {
    armed_ = false;
}

DriveRejection SessionGuard::tryDrive(const char* session, uint64_t seq, uint32_t now_ms) {
    if (!armed_) {
        return DriveRejection::NOT_ARMED;
    }
    if (!sessionMatches(session)) {
        return DriveRejection::WRONG_SESSION;
    }
    if (has_last_seq_ && seq <= last_seq_) {
        return DriveRejection::STALE_SEQUENCE;
    }
    last_seq_ = seq;
    has_last_seq_ = true;
    last_valid_drive_ms_ = now_ms;
    return DriveRejection::ACCEPTED;
}

bool SessionGuard::checkWatchdog(uint32_t now_ms) {
    if (!armed_) {
        return false;
    }
    // now_ms wraps like millis(); unsigned subtraction handles a single
    // wraparound correctly since the timeout is far shorter than the
    // ~49-day wrap period.
    uint32_t elapsed = now_ms - last_valid_drive_ms_;
    if (elapsed > watchdog_timeout_ms_) {
        armed_ = false;
        return true;
    }
    return false;
}

}  // namespace rescuebot
