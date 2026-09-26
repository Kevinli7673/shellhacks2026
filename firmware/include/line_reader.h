#pragma once

#include <cstddef>

namespace rescuebot {

constexpr size_t kMaxLineLength = 200;  // bounded serial line length

// Accumulates bytes into a bounded line buffer until a '\n' is seen.
// Nonblocking: feed() is called once per already-available byte and never
// waits. A line longer than the buffer is dropped and flagged via
// overflowed() so the caller can treat it as malformed/oversized and let
// the buffer resync on the next '\n'.
class LineReader {
public:
    // Returns true when a complete line is ready (line()/length() valid).
    bool feed(char c);

    const char* line() const { return buffer_; }
    size_t length() const { return length_; }
    bool overflowed() const { return overflowed_; }

    void reset();

private:
    char buffer_[kMaxLineLength + 1] = {0};
    size_t length_ = 0;
    bool overflowed_ = false;
};

}  // namespace rescuebot
