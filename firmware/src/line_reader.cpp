#include "line_reader.h"

namespace rescuebot {

bool LineReader::feed(char c) {
    if (c == '\n') {
        buffer_[length_] = '\0';
        return true;
    }
    if (c == '\r') {
        return false;  // tolerate CRLF line endings
    }
    if (length_ >= kMaxLineLength) {
        overflowed_ = true;
        return false;  // drop excess bytes until the next '\n'
    }
    buffer_[length_++] = c;
    return false;
}

void LineReader::reset() {
    length_ = 0;
    overflowed_ = false;
    buffer_[0] = '\0';
}

}  // namespace rescuebot
