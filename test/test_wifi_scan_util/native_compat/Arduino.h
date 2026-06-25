/*
 * Arduino.h — host-only stub for the native test build.
 *
 * src/wifi_scan_util.cpp includes <Arduino.h> only as a transitive
 * gateway to <WiFi.h>'s wifi_auth_mode_t enum. The .cpp itself does
 * not use any Arduino runtime APIs (no Serial, no millis, no pinMode),
 * so this header can stay empty. It exists solely so the include
 * resolves under the native platform.
 *
 * See platformio.ini [env:native] build_flags for the include path
 * that wires this stub in ahead of the real Arduino headers.
 */

#pragma once