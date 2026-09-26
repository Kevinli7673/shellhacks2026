// Test-only host harness: runs the real firmware decision logic (Controller,
// LineReader, protocol codec, session guard, mixing) against a byte stream on
// stdin/stdout, the way main.cpp runs it against Serial on the ESP32-S2.
// Motor outputs the firmware would apply are reported on stderr.
#include <sys/select.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>

#include "controller.h"
#include "line_reader.h"
#include "protocol_messages.h"

using rescuebot::Controller;
using rescuebot::LineReader;

static uint32_t nowMs() {
    using namespace std::chrono;
    static const auto start = steady_clock::now();
    return static_cast<uint32_t>(duration_cast<milliseconds>(steady_clock::now() - start).count());
}

static void sendLine(const char* text, size_t len) {
    if (len == 0) return;
    (void)!write(STDOUT_FILENO, text, len);
    (void)!write(STDOUT_FILENO, "\n", 1);
}

int main(int argc, char** argv) {
    const int ceiling = argc > 1 ? std::atoi(argv[1]) : 255;
    Controller controller(500, ceiling);
    LineReader reader;
    char out[256];
    sendLine(out, controller.boot(out, sizeof(out)));

    int last_armed = -1, lfl = 1 << 30, lfr = 0, lrl = 0, lrr = 0;
    uint32_t last_imu = 0;
    for (;;) {
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(STDIN_FILENO, &fds);
        timeval tv{0, 2000};
        if (select(STDIN_FILENO + 1, &fds, nullptr, nullptr, &tv) > 0) {
            char buf[256];
            ssize_t n = read(STDIN_FILENO, buf, sizeof(buf));
            if (n <= 0) return 0;  // port closed
            for (ssize_t i = 0; i < n; ++i) {
                if (reader.feed(buf[i])) {
                    sendLine(out, controller.handleLine(reader.line(), reader.length(), reader.overflowed(),
                                                        nowMs(), out, sizeof(out)));
                    reader.reset();
                }
            }
        }
        const uint32_t now = nowMs();
        sendLine(out, controller.tick(now, out, sizeof(out)));
        if (now - last_imu >= 50) {
            last_imu = now;
            sendLine(out, rescuebot::buildImuTelemetry(out, sizeof(out), now, true, 90.0, 3));
        }
        const auto& w = controller.outputs();
        const int armed = controller.armed() ? 1 : 0;
        if (armed != last_armed || w.front_left != lfl || w.front_right != lfr || w.rear_left != lrl ||
            w.rear_right != lrr) {
            last_armed = armed; lfl = w.front_left; lfr = w.front_right; lrl = w.rear_left; lrr = w.rear_right;
            std::fprintf(stderr, "MOTORS armed=%d fl=%d fr=%d rl=%d rr=%d\n", armed, lfl, lfr, lrl, lrr);
            std::fflush(stderr);
        }
    }
}
