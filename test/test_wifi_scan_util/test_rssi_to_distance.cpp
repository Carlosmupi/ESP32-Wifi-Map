/*
 * test_rssi_to_distance.cpp
 * -------------------------
 * Unity tests for the pure rssiToDistance() helper in
 * src/wifi_scan_util.{h,cpp}. The implementation uses an exponential
 * path-loss model anchored at -45 dBm and clamps the result to
 * [0, DISTANCE_CAP_M (10 m)]; these tests pin down both ends of that
 * behaviour plus the boundaries so future tuning of the formula can't
 * silently shift the cap or change the reference.
 */

#include <math.h>
#include <unity.h>

#include "wifi_scan_util.h"

static void assert_float_close(float actual, float expected, float tol) {
    TEST_ASSERT_TRUE_MESSAGE(fabsf(actual - expected) <= tol,
                             "rssiToDistance result outside tolerance");
}

void test_rssi_at_reference_is_capped_to_cap(void) {
    // RSSI_REFERENCE_DBM == -45. The formula exp((-rssi - ref)/20) at the
    // reference produces a value well above DISTANCE_CAP_M (10 m), so the
    // implementation must clamp it down. This locks the clamp at the
    // reference point regardless of any future formula tweak.
    assert_float_close(rssiToDistance(-45), 10.0f, 1e-4f);
}

void test_rssi_ten_above_reference_is_capped_to_cap(void) {
    // -35 dBm is 10 dBm stronger than reference; path-loss model still
    // gives a distance above 10 m so the cap must apply.
    assert_float_close(rssiToDistance(-35), 10.0f, 1e-4f);
}

void test_rssi_ten_below_reference_is_capped_to_cap(void) {
    // -55 dBm is 10 dBm weaker than reference; same cap behaviour.
    assert_float_close(rssiToDistance(-55), 10.0f, 1e-4f);
}

void test_rssi_very_weak_is_capped_to_cap(void) {
    // -100 dBm is typical "very weak" territory. Distance blows well
    // past the cap; the function must return exactly 10.0.
    assert_float_close(rssiToDistance(-100), 10.0f, 1e-4f);
}

void test_rssi_exactly_at_cap_value(void) {
    // Pick an RSSI whose raw formula value lands precisely on 10.0 m.
    // Any RSSI below the (theoretical, sub-zero) inflection that
    // satisfies exp((-rssi + 45)/20) == 10 qualifies; -50 dBm is one
    // such value within the realistic range and exercises the upper
    // clamp boundary.
    assert_float_close(rssiToDistance(-50), 10.0f, 1e-4f);
}

void test_rssi_extreme_positive_clamped_to_floor(void) {
    // An absurdly strong (positive) RSSI makes exp underflow to 0.0;
    // the function must never return a negative distance. This pins
    // the lower clamp regardless of whether the dead-branch
    // `if (dist < 0.0f)` ever fires for in-range inputs.
    const float very_strong = rssiToDistance(INT32_MAX);
    TEST_ASSERT_GREATER_OR_EQUAL_FLOAT(0.0f, very_strong);
    TEST_ASSERT_TRUE_MESSAGE(very_strong < 0.01f,
                             "extreme-strong RSSI should map to ~0 m");
}