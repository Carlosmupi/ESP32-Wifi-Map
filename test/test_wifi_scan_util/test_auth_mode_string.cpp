/*
 * test_auth_mode_string.cpp
 * -------------------------
 * Unity tests for the pure authModeString() helper in
 * src/wifi_scan_util.{h,cpp}. The function takes a uint8_t (the raw
 * byte returned by WiFi.encryptionType()), casts it to wifi_auth_mode_t
 * inside the .cpp, and returns a stable short token for CSV output.
 *
 * Integer literals 0..9 mirror the ESP32 Arduino SDK 3.x enum layout
 * (WIFI_AUTH_OPEN ... WIFI_AUTH_WPA3_ENT_192); the test deliberately
 * does not include <WiFi.h> so the values stay explicit and the test
 * stays portable to any host build.
 */

#include <unity.h>

#include "wifi_scan_util.h"

void test_auth_mode_open_returns_OPEN(void) {
    TEST_ASSERT_EQUAL_STRING("OPEN", authModeString(0));
}

void test_auth_mode_wep_returns_WEP(void) {
    TEST_ASSERT_EQUAL_STRING("WEP", authModeString(1));
}

void test_auth_mode_wpa_psk_returns_WPA_PSK(void) {
    TEST_ASSERT_EQUAL_STRING("WPA_PSK", authModeString(2));
}

void test_auth_mode_wpa2_psk_returns_WPA2_PSK(void) {
    TEST_ASSERT_EQUAL_STRING("WPA2_PSK", authModeString(3));
}

void test_auth_mode_wpa_wpa2_psk_returns_WPA_WPA2_PSK(void) {
    TEST_ASSERT_EQUAL_STRING("WPA_WPA2_PSK", authModeString(4));
}

void test_auth_mode_wpa2_enterprise_returns_WPA2_ENT(void) {
    // The firmware deliberately emits the short token "WPA2_ENT"
    // (not the SDK's "WPA2_ENTERPRISE") to keep the CSV column compact.
    TEST_ASSERT_EQUAL_STRING("WPA2_ENT", authModeString(5));
}

void test_auth_mode_wpa3_psk_returns_WPA3_PSK(void) {
    TEST_ASSERT_EQUAL_STRING("WPA3_PSK", authModeString(6));
}

void test_auth_mode_wpa2_wpa3_psk_returns_WPA2_WPA3_PSK(void) {
    TEST_ASSERT_EQUAL_STRING("WPA2_WPA3_PSK", authModeString(7));
}

void test_auth_mode_wapi_psk_returns_WAPI_PSK(void) {
    TEST_ASSERT_EQUAL_STRING("WAPI_PSK", authModeString(8));
}

void test_auth_mode_wpa3_ent_192_returns_WPA3_ENT_192(void) {
    TEST_ASSERT_EQUAL_STRING("WPA3_ENT_192", authModeString(9));
}

void test_auth_mode_unknown_returns_UNKNOWN(void) {
    // Out-of-range values (e.g. reserved or future enum additions) must
    // fall through to the default case and yield "UNKNOWN" so capture.py
    // never sees a blank or garbage auth_mode cell.
    TEST_ASSERT_EQUAL_STRING("UNKNOWN", authModeString(200));
    TEST_ASSERT_EQUAL_STRING("UNKNOWN", authModeString(255));
}