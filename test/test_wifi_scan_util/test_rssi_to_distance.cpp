/*
 * test_rssi_to_distance.cpp
 * -------------------------
 * Unity tests for the pure rssiToDistance() helper in
 * src/wifi_scan_util.{h,cpp}. The implementation uses an exponential
 * path-loss model: exp((-rssi - RSSI_REFERENCE_DBM) / 20), where
 * RSSI_REFERENCE_DBM is 45 (positive — corrected in commit 6b205da
 * from the prior -45 which inverted the distance/signal relationship).
 * The result is clamped to [0, DISTANCE_CAP_M (10 m)].
 *
 * These tests pin down the reference point, the clamp boundaries, and
 * the monotonicity (stronger signal → shorter distance) so future
 * tuning of the formula can't silently shift the cap or break the
 * sign convention.
 */

#include <math.h>
#include <unity.h>

#include "wifi_scan_util.h"

static void assert_float_close(float actual, float expected, float tol) {
    TEST_ASSERT_TRUE_MESSAGE(fabsf(actual - expected) <= tol,
                             "rssiToDistance result outside tolerance");
}

void test_rssi_at_reference_returns_one_meter(void) {
    // RSSI_REFERENCE_DBM == 45 (positive). At rssi = -45 the formula
    // gives exp((-(-45) - 45) / 20) = exp(0) = 1.0 m — the natural
    // reference distance of one meter.
    assert_float_close(rssiToDistance(-45), 1.0f, 1e-4f);
}

void test_rssi_ten_above_reference_is_closer(void) {
    // -35 dBm is 10 dBm stronger than the reference. A stronger signal
    // means the transmitter is closer: exp((-10) / 20) = exp(-0.5)
    // ≈ 0.6065 m.
    assert_float_close(rssiToDistance(-35), 0.606531f, 1e-3f);
}

void test_rssi_ten_below_reference_is_farther(void) {
    // -55 dBm is 10 dBm weaker than the reference. A weaker signal
    // means the transmitter is farther: exp(10 / 20) = exp(0.5)
    // ≈ 1.6487 m.
    assert_float_close(rssiToDistance(-55), 1.648721f, 1e-3f);
}

void test_rssi_very_weak_is_capped_to_cap(void) {
    // -100 dBm is typical "very weak" territory. Distance blows well
    // past the cap; the function must return exactly 10.0.
    assert_float_close(rssiToDistance(-100), 10.0f, 1e-4f);
}

void test_rssi_at_cap_boundary_clamps(void) {
    // -92 dBm: exp((92 - 45) / 20) = exp(2.35) ≈ 10.49, just above the
    // 10 m cap, so the clamp must bring it down to exactly 10.0.
    // (-91 would give ≈ 9.97, just under the cap — not clamped.)
    assert_float_close(rssiToDistance(-92), 10.0f, 1e-4f);
}

void test_rssi_extreme_positive_clamped_to_floor(void) {
    // An absurdly strong (positive) RSSI makes exp underflow to 0.0;
    // the function must never return a negative distance. Uses 10000
    // dBm rather than INT32_MAX to avoid signed integer overflow in
    // the expression `-rssi_dbm - RSSI_REFERENCE_DBM` (INT32_MAX would
    // make -INT32_MAX - 45 underflow past INT32_MIN, which is UB and
    // produces different results on different platforms).
    const float very_strong = rssiToDistance(10000);
    TEST_ASSERT_GREATER_OR_EQUAL_FLOAT(0.0f, very_strong);
    TEST_ASSERT_TRUE_MESSAGE(very_strong < 0.01f,
                             "extreme-strong RSSI should map to ~0 m");
}
