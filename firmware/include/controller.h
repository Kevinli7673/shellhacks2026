#pragma once

#include <cstddef>
#include <cstdint>

#include "mixing.h"
#include "protocol_messages.h"
#include "session_guard.h"

namespace rescuebot {

// Orchestrates one processed serial line against the safety state machine
// and the pure mixing calculation. Hardware-agnostic and zero-dependency
// beyond the modules above, so the exact same logic is exercised by the
// real firmware (main.cpp) and by native tests (see
// firmware/test/test_protocol_fixtures), rather than main.cpp duplicating
// this decision logic in a way that could drift from what is tested.
//
// Callers own: the LineReader that turns bytes into bounded lines, the
// actual hardware writes (apply wiring.h to outputs() and write via
// MotorShield), and deciding when to call tick().
class Controller {
public:
    Controller(uint32_t watchdog_timeout_ms, int hardware_pwm_ceiling);

    // Call once at boot, after real motor outputs are already forced off
    // (IMPLEMENTATION_PLAN.md section 7). Writes the boot fault message
    // ({"type":"fault","reason":"boot","armed":false}) and returns its
    // length.
    size_t boot(char* out, size_t out_size);

    // Processes one bounded, newline-stripped line. If `overflowed` is
    // true (the caller's LineReader flagged this line as oversized),
    // `line`/`length` are ignored and it is treated as a malformed/
    // oversized packet. Writes at most one outbound message to `out` and
    // returns its length, or 0 if nothing should be sent.
    size_t handleLine(const char* line, size_t length, bool overflowed, uint32_t now_ms,
                       char* out, size_t out_size);

    // Call every loop iteration (or simulated tick in a test). Writes the
    // watchdog-expired fault message and returns its length if the
    // watchdog just tripped this call; returns 0 otherwise.
    size_t tick(uint32_t now_ms, char* out, size_t out_size);

    bool armed() const { return guard_.armed(); }

    // The last computed wheel outputs, pre-wiring (see wiring.h). Zeroed
    // on boot, disarm, any fault, and any accepted arm.
    const WheelOutputs& outputs() const { return outputs_; }

    void setHardwarePwmCeiling(int ceiling) { hardware_pwm_ceiling_ = ceiling; }

private:
    size_t handleDrive(const InboundDrive& drive, uint32_t now_ms, char* out, size_t out_size);

    SessionGuard guard_;
    int hardware_pwm_ceiling_;
    WheelOutputs outputs_;
};

}  // namespace rescuebot
