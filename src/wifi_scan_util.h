/*
 * wifi_scan_util.h
 * ----------------
 * Pure, Arduino-free helpers extracted from src/main.cpp so they can be
 * unit-tested without dragging in the Arduino framework or a connected
 * board. Only the standard headers <stddef.h>, <stdint.h>, and <math.h>
 * are pulled in — no <Arduino.h>, no <WiFi.h>.
 */

#pragma once

#include <stddef.h>
#include <stdint.h>
#include <math.h>

// Map a numeric wifi_auth_mode_t value (passed as uint8_t) to a stable,
// short token suitable for CSV output. The cast to the Arduino enum type
// is performed inside the .cpp implementation, so this header stays
// Arduino-free.
const char* authModeString(uint8_t auth);

// CSV-escape a NUL-terminated field per RFC 4180. Returns a heap-allocated,
// NUL-terminated string the caller MUST free() — replaces the prior
// Arduino String return type so this header stays free of <Arduino.h>.
// A NULL or empty field yields a freshly-allocated empty string ("\0").
char* csvEscape(const char* field);

// RSSI (dBm) -> rough distance in meters, capped at DISTANCE_CAP_M.
// The result is clamped to [0, DISTANCE_CAP_M].
float rssiToDistance(int32_t rssi_dbm);

// Copy up to (dest_size - 1) bytes from src into dest, NUL-terminating.
// Embedded NULs in src stop the copy early (preserving the prior
// Arduino-String behavior of treating `length()` as the upper bound).
// dest_size must be > 0; src may be NULL (no-op).
void copyLabel(char* dest, size_t dest_size, const char* src);

// Tiny testable seams so firmware code that depends on time or a digital
// input can be unit-tested without dragging in the Arduino framework or a
// connected board. The header stays free of <Arduino.h>: only the
// declarations live here; the Arduino-coupled adapter bodies are in
// src/io_abstractions.cpp.
class Clock {
public:
    virtual uint32_t now_ms() = 0;
    virtual ~Clock() = default;
};

class Pin {
public:
    virtual int read() = 0;
    virtual ~Pin() = default;
};

// Arduino adapters. Declarations are framework-free so anything that only
// needs the type can include this header without pulling in <Arduino.h>.
// Bodies live in src/io_abstractions.cpp.
class ArduinoClock : public Clock {
public:
    uint32_t now_ms() override;
};

class ArduinoPin : public Pin {
public:
    explicit ArduinoPin(uint8_t pin);
    int read() override;
private:
    uint8_t pin_;
};
