#include "mixing.h"

#include <algorithm>
#include <cmath>

namespace rescuebot {

namespace {

int roundHalfAwayFromZero(double value) {
    return static_cast<int>(std::lround(value));
}

}  // namespace

WheelOutputs mix(double forward, double sideways, double turn, int speed_limit) {
    // Explicit zero-limit handling (IMPLEMENTATION_PLAN.md section 4).
    if (speed_limit <= 0) {
        return WheelOutputs{0, 0, 0, 0};
    }

    // Step 1: normalize the forward/sideways vector if it exceeds unit
    // magnitude. turn is independently bounded to [-1, 1] by the caller
    // and is not part of this normalization.
    double magnitude = std::sqrt(forward * forward + sideways * sideways);
    if (magnitude > 1.0) {
        forward /= magnitude;
        sideways /= magnitude;
    }

    // Step 2: scale forward, sideways, and turn by the effective PWM limit.
    double f = forward * speed_limit;
    double s = sideways * speed_limit;
    double t = turn * speed_limit;

    // Step 3: the exact approved mixing equations. Do not change these;
    // correct physical wheel direction via wiring.h configuration instead.
    double fl = f + s + t;
    double fr = f - s - t;
    double rl = f - s + t;
    double rr = f + s - t;

    // Step 4: if any absolute output exceeds the limit, scale all four
    // together so their ratios are preserved.
    double max_abs = std::max({std::fabs(fl), std::fabs(fr), std::fabs(rl), std::fabs(rr)});
    if (max_abs > speed_limit) {
        double scale = speed_limit / max_abs;
        fl *= scale;
        fr *= scale;
        rl *= scale;
        rr *= scale;
    }

    // Step 5: round consistently, then clamp defensively in case
    // floating-point rounding pushed a value one unit past the limit.
    WheelOutputs out;
    out.front_left = std::clamp(roundHalfAwayFromZero(fl), -speed_limit, speed_limit);
    out.front_right = std::clamp(roundHalfAwayFromZero(fr), -speed_limit, speed_limit);
    out.rear_left = std::clamp(roundHalfAwayFromZero(rl), -speed_limit, speed_limit);
    out.rear_right = std::clamp(roundHalfAwayFromZero(rr), -speed_limit, speed_limit);
    return out;
}

}  // namespace rescuebot
