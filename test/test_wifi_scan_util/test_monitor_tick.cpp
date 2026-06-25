/*
 * test_monitor_tick.cpp
 * ---------------------
 * Unity tests for the inline `monitorTick` helper in
 * src/wifi_scan_util.h. Host-only; does not require the ESP32 toolchain.
 *
 * Follows the suite's existing convention: test functions have external
 * linkage and are wired into Unity by test_main.cpp (the single TU that
 * defines main()). setUp()/tearDown() are intentionally NOT defined here
 * — they are provided explicitly in test_main.cpp (see pre-flight fix
 * for the cross-platform reason: PE/COFF weak symbols don't resolve
 * the way ELF does).
 */

#include <unity.h>

#include "wifi_scan_util.h"  // already brings in <stdint.h> for uint32_t

void test_first_call_after_interval_fires(void) {
    // Interval of 1000 ms; last scan at t=0; now at t=1000.
    TEST_ASSERT_TRUE(monitorTick(0u, 1000u, 1000u));
}

void test_just_before_interval_does_not_fire(void) {
    TEST_ASSERT_FALSE(monitorTick(0u, 999u, 1000u));
}

void test_repeat_call_without_time_advance_does_not_fire(void) {
    // Real invariant: if time hasn't advanced since the last check
    // (now == last_scan_ms), monitorTick must return false. A
    // background loop that polls faster than the interval will not
    // double-fire. The two-assertion form was a category error —
    // calling a pure function twice with identical args must return
    // the same value, not magically "consume" the first call.
    TEST_ASSERT_FALSE(monitorTick(1000u, 1000u, 1000u));
}

void test_unsigned_rollover_fires(void) {
    // 2^32 - 1 ms after the last scan should still fire.
    const uint32_t last = 1u;
    const uint32_t now  = 0u;  // wrapped
    TEST_ASSERT_TRUE(monitorTick(last, now, 1000u));
}

void test_zero_interval_fires_every_call(void) {
    TEST_ASSERT_TRUE(monitorTick(0u, 0u, 0u));
    TEST_ASSERT_TRUE(monitorTick(0u, 1u, 0u));
}
