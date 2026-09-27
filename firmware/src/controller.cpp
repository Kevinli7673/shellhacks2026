#include "controller.h"

#include <cstring>

namespace rescuebot {

Controller::Controller(uint32_t watchdog_timeout_ms, int hardware_pwm_ceiling)
    : guard_(watchdog_timeout_ms), hardware_pwm_ceiling_(hardware_pwm_ceiling) {}

size_t Controller::boot(char* out, size_t out_size) {
    guard_.reset();
    outputs_ = WheelOutputs{};
    accessories_ = Accessories{};
    accessory_session_[0] = '\0';
    accessory_seq_ = 0;
    return buildFault(out, out_size, "boot");
}

size_t Controller::handleAccessories(const InboundAccessories& command, char* out,
                                      size_t out_size) {
    if (std::strncmp(command.session, accessory_session_, kMaxSessionLength) == 0 &&
        command.seq <= accessory_seq_) {
        return 0;  // stale or replayed within the same session: no ack, no change
    }
    std::strncpy(accessory_session_, command.session, kMaxSessionLength - 1);
    accessory_session_[kMaxSessionLength - 1] = '\0';
    accessory_seq_ = command.seq;
    accessories_.buzzer = command.buzzer;
    accessories_.light = command.light;
    return buildAccessoriesAck(out, out_size, command.session, command.seq, accessories_.buzzer,
                                accessories_.light);
}

size_t Controller::handleDrive(const InboundDrive& drive, uint32_t now_ms, char* out,
                                size_t out_size) {
    DriveRejection result = guard_.tryDrive(drive.session, drive.seq, now_ms);
    if (result != DriveRejection::ACCEPTED) {
        // Stale/duplicate/out-of-order/wrong-session/not-armed: ignore. Do
        // not drive motors, do not refresh the watchdog, do not disarm
        // (IMPLEMENTATION_PLAN.md section 6).
        return 0;
    }

    int limit = drive.speed_limit;
    if (limit > hardware_pwm_ceiling_) {
        limit = hardware_pwm_ceiling_;
    }
    outputs_ = mix(drive.forward, drive.sideways, drive.turn, limit);
    return buildDriveAck(out, out_size, drive.session, drive.seq, outputs_.front_left,
                          outputs_.front_right, outputs_.rear_left, outputs_.rear_right);
}

size_t Controller::handleLine(const char* line, size_t length, bool overflowed, uint32_t now_ms,
                               char* out, size_t out_size) {
    if (overflowed) {
        guard_.faultDisarm();
        outputs_ = WheelOutputs{};
        return buildFault(out, out_size, "oversized_packet");
    }

    InboundMessage msg = parseInbound(line, length);
    if (msg.malformed) {
        guard_.faultDisarm();
        outputs_ = WheelOutputs{};
        return buildFault(out, out_size, "malformed_packet");
    }

    switch (msg.type) {
        case InboundType::DRIVE:
            return handleDrive(msg.drive, now_ms, out, out_size);

        case InboundType::ARM: {
            bool accepted = guard_.arm(msg.arm_disarm.session, msg.arm_disarm.seq, now_ms);
            if (!accepted) {
                // Stale/replayed arm (same session, seq not higher than
                // the last one seen): ignored, no ack, no motor change.
                return 0;
            }
            // Nothing moves until the first new drive packet, even if the
            // previous session left the motors running.
            outputs_ = WheelOutputs{};
            return buildArmAck(out, out_size, msg.arm_disarm.session, msg.arm_disarm.seq, true);
        }

        case InboundType::DISARM:
            guard_.disarm();
            outputs_ = WheelOutputs{};
            return buildDisarmAck(out, out_size, msg.arm_disarm.session, msg.arm_disarm.seq);

        case InboundType::ACCESSORIES:
            return handleAccessories(msg.accessories, out, out_size);

        case InboundType::UNKNOWN:
            // parseInbound flags genuinely unrecognized "type" values as
            // malformed above, so this case is unreachable in practice.
            return 0;
    }
    return 0;
}

size_t Controller::tick(uint32_t now_ms, char* out, size_t out_size) {
    if (guard_.checkWatchdog(now_ms)) {
        outputs_ = WheelOutputs{};
        return buildFault(out, out_size, "watchdog_expired");
    }
    return 0;
}

}  // namespace rescuebot
