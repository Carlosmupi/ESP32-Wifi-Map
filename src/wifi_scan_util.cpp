/*
 * wifi_scan_util.cpp
 * ------------------
 * Implementations for the pure helpers declared in wifi_scan_util.h.
 * These mirror the bodies previously inlined inside the anonymous
 * namespace of src/main.cpp.
 */

#include "wifi_scan_util.h"

#include <stdlib.h>
#include <string.h>

// Arduino headers are kept inside the .cpp so the header stays
// framework-free. wifi_auth_mode_t values are required for the
// static_cast inside authModeString().
#include <Arduino.h>
#include <WiFi.h>

namespace {

// RSSI -> rough distance parameters (path-loss formula used by
// Wifi-Radar-Scanner-for-ESP32). Distance is capped so a very weak RSSI
// never produces a misleadingly large value.
constexpr int16_t  RSSI_REFERENCE_DBM   = -45;
constexpr float    DISTANCE_CAP_M       = 10.0f;
constexpr float    DISTANCE_SCALE       = 20.0f;

}  // namespace

const char* authModeString(uint8_t auth) {
    switch (static_cast<wifi_auth_mode_t>(auth)) {
        case WIFI_AUTH_OPEN:             return "OPEN";
        case WIFI_AUTH_WEP:              return "WEP";
        case WIFI_AUTH_WPA_PSK:          return "WPA_PSK";
        case WIFI_AUTH_WPA2_PSK:         return "WPA2_PSK";
        case WIFI_AUTH_WPA_WPA2_PSK:     return "WPA_WPA2_PSK";
        case WIFI_AUTH_WPA2_ENTERPRISE:  return "WPA2_ENT";
        case WIFI_AUTH_WPA3_PSK:         return "WPA3_PSK";
        case WIFI_AUTH_WPA2_WPA3_PSK:    return "WPA2_WPA3_PSK";
        case WIFI_AUTH_WAPI_PSK:         return "WAPI_PSK";
        case WIFI_AUTH_WPA3_ENT_192:     return "WPA3_ENT_192";
        default:                         return "UNKNOWN";
    }
}

char* csvEscape(const char* field) {
    if (field == nullptr || field[0] == '\0') {
        char* empty = static_cast<char*>(malloc(1));
        if (empty != nullptr) {
            empty[0] = '\0';
        }
        return empty;
    }

    const size_t in_len = strlen(field);

    bool needs_quote = false;
    for (size_t i = 0; i < in_len; ++i) {
        const char c = field[i];
        if (c == ',' || c == '"' || c == '\n' || c == '\r') {
            needs_quote = true;
            break;
        }
    }
    if (!needs_quote) {
        char* out = static_cast<char*>(malloc(in_len + 1));
        if (out != nullptr) {
            memcpy(out, field, in_len + 1);
        }
        return out;
    }

    // Worst-case size: every char becomes two (doubled inner quotes)
    // plus two surrounding quotes plus NUL.
    char* out = static_cast<char*>(malloc(in_len * 2 + 3));
    if (out == nullptr) {
        return nullptr;
    }

    size_t w = 0;
    out[w++] = '"';
    for (size_t i = 0; i < in_len; ++i) {
        const char c = field[i];
        if (c == '"') {
            out[w++] = '"';
            out[w++] = '"';
        } else {
            out[w++] = c;
        }
    }
    out[w++] = '"';
    out[w]   = '\0';
    return out;
}

float rssiToDistance(int32_t rssi_dbm) {
    float dist = exp(static_cast<float>(-rssi_dbm - RSSI_REFERENCE_DBM) /
                     DISTANCE_SCALE);
    if (dist > DISTANCE_CAP_M) dist = DISTANCE_CAP_M;
    if (dist < 0.0f)           dist = 0.0f;
    return dist;
}

void copyLabel(char* dest, size_t dest_size, const char* src) {
    if (dest == nullptr || dest_size == 0 || src == nullptr) {
        return;
    }
    size_t w = 0;
    for (size_t i = 0; (i + 1) < dest_size && src[i] != '\0'; ++i) {
        dest[w++] = src[i];
    }
    dest[w] = '\0';
}
