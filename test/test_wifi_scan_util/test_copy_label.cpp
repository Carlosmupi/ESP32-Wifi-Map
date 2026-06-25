/*
 * test_copy_label.cpp
 * -------------------
 * Unity tests for the pure copyLabel() helper in src/wifi_scan_util.{h,cpp}.
 * Mirrors the legacy Arduino-String length()-bounded semantics: copy up to
 * (dest_size - 1) bytes, stop at an embedded NUL, NUL-terminate the
 * destination, and treat NULL src / zero dest_size as no-ops.
 *
 * The buffer is sized large enough for the longest expected copy plus a
 * generous over-run region that is initialised to a sentinel byte
 * ('Z'). This lets each test assert that copyLabel never writes past
 * the intended NUL terminator — important for the firmware, which
 * shares the destination buffer across scans.
 */

#include <string.h>
#include <unity.h>

#include "wifi_scan_util.h"

#define COPY_LABEL_BUF_SIZE 32

static void run_copy(const char* src, size_t dest_size, char* dest) {
    // Sentinel-fill the WHOLE destination buffer so the byte positions
    // copyLabel is not supposed to touch retain a known value. The
    // over-cap test relies on dest[dest_size] still being 'Z' after
    // the copy terminates to prove the implementation's
    // (i + 1) < dest_size loop bound stops in time.
    memset(dest, 'Z', COPY_LABEL_BUF_SIZE);
    copyLabel(dest, dest_size, src);
}

void test_copy_label_short_input_is_copied_verbatim(void) {
    char buf[COPY_LABEL_BUF_SIZE];
    run_copy("ab", 10, buf);
    TEST_ASSERT_EQUAL_STRING("ab", buf);
}

void test_copy_label_exact_cap_input_fills_buffer(void) {
    // dest_size == 5 means copyLabel writes 4 chars + 1 NUL — the
    // exact-cap case where the source length matches (dest_size - 1).
    char buf[COPY_LABEL_BUF_SIZE];
    run_copy("abcd", 5, buf);
    TEST_ASSERT_EQUAL_STRING("abcd", buf);
    TEST_ASSERT_EQUAL_UINT8('\0', buf[4]);
}

void test_copy_label_over_cap_input_is_truncated(void) {
    // "abcdefgh" through dest_size == 5 must truncate to "abcd\0".
    char buf[COPY_LABEL_BUF_SIZE];
    run_copy("abcdefgh", 5, buf);
    TEST_ASSERT_EQUAL_STRING("abcd", buf);
    TEST_ASSERT_EQUAL_UINT8('\0', buf[4]);
    // The byte immediately past the terminator must still be the
    // untouched sentinel 'Z' from run_copy's memset — proving the
    // implementation's (i + 1) < dest_size loop bound stops in time.
    TEST_ASSERT_EQUAL_UINT8('Z', buf[5]);
}

void test_copy_label_embedded_nul_stops_copy_early(void) {
    // Embedded NUL in src must terminate the copy — this matches the
    // legacy Arduino-String length()-bounded behaviour the firmware
    // relied on before extraction.
    char buf[COPY_LABEL_BUF_SIZE];
    const char src[] = {'a', 'b', '\0', 'c', 'd', '\0'};
    run_copy(src, sizeof(src), buf);
    TEST_ASSERT_EQUAL_STRING("ab", buf);
}

void test_copy_label_null_src_is_a_no_op(void) {
    // Pre-fill the destination with a known sentinel; copyLabel must
    // leave it untouched when src is NULL.
    char buf[COPY_LABEL_BUF_SIZE];
    memset(buf, 'X', sizeof(buf));
    copyLabel(buf, sizeof(buf), NULL);
    for (size_t i = 0; i < sizeof(buf); ++i) {
        TEST_ASSERT_EQUAL_UINT8_MESSAGE('X', buf[i], "NULL src must not mutate dest");
    }
}

void test_copy_label_zero_dest_size_is_a_no_op(void) {
    // dest_size == 0 must short-circuit before any write to dest so
    // callers can safely pass a stack buffer in degenerate cases.
    char buf[COPY_LABEL_BUF_SIZE];
    memset(buf, 'Y', sizeof(buf));
    copyLabel(buf, 0, "should not be copied");
    for (size_t i = 0; i < sizeof(buf); ++i) {
        TEST_ASSERT_EQUAL_UINT8_MESSAGE('Y', buf[i], "dest_size==0 must not mutate dest");
    }
}