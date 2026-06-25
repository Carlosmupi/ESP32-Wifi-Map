/*
 * test_main.cpp
 * --------------
 * Single Unity main() entry point for the test/test_wifi_scan_util/
 * suite. PlatformIO's native test build links every test_*.cpp in
 * the directory into one binary, so only one translation unit may
 * define main(); the per-function files declare their test_xxx()
 * routines here and we wire them into Unity here.
 *
 * setUp() and tearDown() are defined here as empty functions. The
 * weak empty versions emitted by unity_config.c do not resolve
 * correctly on Windows/MinGW (PE format handles weak symbols in
 * object files differently from ELF), so we provide explicit
 * definitions that work on all platforms.
 */

#include <unity.h>

#include "wifi_scan_util.h"

void setUp(void) {}
void tearDown(void) {}

// ---- csvEscape (test_csv_escape.cpp) ----------------------------------
extern void test_csv_escape_empty_string_yields_empty_alloc(void);
extern void test_csv_escape_plain_string_is_returned_verbatim(void);
extern void test_csv_escape_embedded_comma_wraps_in_quotes(void);
extern void test_csv_escape_embedded_quote_doubles_inner_quote(void);
extern void test_csv_escape_embedded_newline_wraps_in_quotes(void);
extern void test_csv_escape_embedded_cr_wraps_in_quotes(void);
extern void test_csv_escape_mixed_special_chars_quotes_and_doubles(void);

// ---- rssiToDistance (test_rssi_to_distance.cpp) -----------------------
// Updated in commit 6b205da: RSSI_REFERENCE_DBM sign corrected from
// -45 to 45; tests renamed to match the corrected behaviour.
extern void test_rssi_at_reference_returns_one_meter(void);
extern void test_rssi_ten_above_reference_is_closer(void);
extern void test_rssi_ten_below_reference_is_farther(void);
extern void test_rssi_very_weak_is_capped_to_cap(void);
extern void test_rssi_at_cap_boundary_clamps(void);
extern void test_rssi_extreme_positive_clamped_to_floor(void);

// ---- authModeString (test_auth_mode_string.cpp) -----------------------
extern void test_auth_mode_open_returns_OPEN(void);
extern void test_auth_mode_wep_returns_WEP(void);
extern void test_auth_mode_wpa_psk_returns_WPA_PSK(void);
extern void test_auth_mode_wpa2_psk_returns_WPA2_PSK(void);
extern void test_auth_mode_wpa_wpa2_psk_returns_WPA_WPA2_PSK(void);
extern void test_auth_mode_wpa2_enterprise_returns_WPA2_ENT(void);
extern void test_auth_mode_wpa3_psk_returns_WPA3_PSK(void);
extern void test_auth_mode_wpa2_wpa3_psk_returns_WPA2_WPA3_PSK(void);
extern void test_auth_mode_wapi_psk_returns_WAPI_PSK(void);
extern void test_auth_mode_wpa3_ent_192_returns_WPA3_ENT_192(void);
extern void test_auth_mode_unknown_returns_UNKNOWN(void);

// ---- copyLabel (test_copy_label.cpp) ----------------------------------
extern void test_copy_label_short_input_is_copied_verbatim(void);
extern void test_copy_label_exact_cap_input_fills_buffer(void);
extern void test_copy_label_over_cap_input_is_truncated(void);
extern void test_copy_label_embedded_nul_stops_copy_early(void);
extern void test_copy_label_null_src_is_a_no_op(void);
extern void test_copy_label_zero_dest_size_is_a_no_op(void);

int main(int /*argc*/, char** /*argv*/) {
    UNITY_BEGIN();

    // csvEscape: RFC 4180 escaping for spot labels and SSIDs.
    RUN_TEST(test_csv_escape_empty_string_yields_empty_alloc);
    RUN_TEST(test_csv_escape_plain_string_is_returned_verbatim);
    RUN_TEST(test_csv_escape_embedded_comma_wraps_in_quotes);
    RUN_TEST(test_csv_escape_embedded_quote_doubles_inner_quote);
    RUN_TEST(test_csv_escape_embedded_newline_wraps_in_quotes);
    RUN_TEST(test_csv_escape_embedded_cr_wraps_in_quotes);
    RUN_TEST(test_csv_escape_mixed_special_chars_quotes_and_doubles);

    // rssiToDistance: path-loss model capped at DISTANCE_CAP_M.
    // Test names updated for the corrected sign convention (6b205da).
    RUN_TEST(test_rssi_at_reference_returns_one_meter);
    RUN_TEST(test_rssi_ten_above_reference_is_closer);
    RUN_TEST(test_rssi_ten_below_reference_is_farther);
    RUN_TEST(test_rssi_very_weak_is_capped_to_cap);
    RUN_TEST(test_rssi_at_cap_boundary_clamps);
    RUN_TEST(test_rssi_extreme_positive_clamped_to_floor);

    // authModeString: stable CSV token for every ESP32 wifi_auth_mode_t
    // value 0..9 plus the UNKNOWN fall-through for out-of-range inputs.
    RUN_TEST(test_auth_mode_open_returns_OPEN);
    RUN_TEST(test_auth_mode_wep_returns_WEP);
    RUN_TEST(test_auth_mode_wpa_psk_returns_WPA_PSK);
    RUN_TEST(test_auth_mode_wpa2_psk_returns_WPA2_PSK);
    RUN_TEST(test_auth_mode_wpa_wpa2_psk_returns_WPA_WPA2_PSK);
    RUN_TEST(test_auth_mode_wpa2_enterprise_returns_WPA2_ENT);
    RUN_TEST(test_auth_mode_wpa3_psk_returns_WPA3_PSK);
    RUN_TEST(test_auth_mode_wpa2_wpa3_psk_returns_WPA2_WPA3_PSK);
    RUN_TEST(test_auth_mode_wapi_psk_returns_WAPI_PSK);
    RUN_TEST(test_auth_mode_wpa3_ent_192_returns_WPA3_ENT_192);
    RUN_TEST(test_auth_mode_unknown_returns_UNKNOWN);

    // copyLabel: bounded, NUL-terminating copy with embedded-NUL + NULL
    // safety mirrors the legacy Arduino-String length()-bounded behaviour.
    RUN_TEST(test_copy_label_short_input_is_copied_verbatim);
    RUN_TEST(test_copy_label_exact_cap_input_fills_buffer);
    RUN_TEST(test_copy_label_over_cap_input_is_truncated);
    RUN_TEST(test_copy_label_embedded_nul_stops_copy_early);
    RUN_TEST(test_copy_label_null_src_is_a_no_op);
    RUN_TEST(test_copy_label_zero_dest_size_is_a_no_op);

    return UNITY_END();
}