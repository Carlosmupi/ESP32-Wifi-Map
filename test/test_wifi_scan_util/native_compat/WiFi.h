/*
 * WiFi.h — host-only stub for the native test build.
 *
 * Mirrors the subset of esp_wifi_types.h that src/wifi_scan_util.cpp
 * actually touches: the wifi_auth_mode_t enum (consumed by the
 * static_cast inside authModeString()). Enum integer values match the
 * ESP32 Arduino SDK 3.x layout so the function returns identical
 * strings for identical inputs under both the real firmware and the
 * native test binary.
 *
 * See platformio.ini [env:native] build_flags for the include path
 * that wires this stub in ahead of the real WiFi headers.
 */

#pragma once

#include <stdint.h>

typedef enum {
    WIFI_AUTH_OPEN             = 0,
    WIFI_AUTH_WEP              = 1,
    WIFI_AUTH_WPA_PSK          = 2,
    WIFI_AUTH_WPA2_PSK         = 3,
    WIFI_AUTH_WPA_WPA2_PSK     = 4,
    WIFI_AUTH_WPA2_ENTERPRISE  = 5,
    WIFI_AUTH_WPA3_PSK         = 6,
    WIFI_AUTH_WPA2_WPA3_PSK    = 7,
    WIFI_AUTH_WAPI_PSK         = 8,
    WIFI_AUTH_WPA3_ENT_192     = 9,
} wifi_auth_mode_t;