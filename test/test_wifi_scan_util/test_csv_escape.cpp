/*
 * test_csv_escape.cpp
 * -------------------
 * Unity tests for the pure csvEscape() helper in src/wifi_scan_util.{h,cpp}.
 * Covers the RFC 4180 corner cases that the firmware encounters when
 * labelling spots ("kitchen", "office", etc.) and printing user-provided
 * SSIDs that may legitimately contain commas, quotes, or newlines.
 *
 * The function heap-allocates the result; every TEST_ASSERT_EQUAL_STRING
 * below is paired with a free() of the returned pointer.
 */

#include <stdlib.h>
#include <unity.h>

#include "wifi_scan_util.h"

static void assert_escape_equals(const char* input, const char* expected) {
    char* out = csvEscape(input);
    TEST_ASSERT_NOT_NULL_MESSAGE(out, "csvEscape returned NULL");
    // TEST_ASSERT_EQUAL_STRING already compares lengths (strings must
    // match up to and including the terminating NUL).
    TEST_ASSERT_EQUAL_STRING_MESSAGE(expected, out, "csvEscape mismatch");
    free(out);
}

void test_csv_escape_empty_string_yields_empty_alloc(void) {
    // A NUL-terminated empty input must allocate a fresh empty string
    // (not return NULL) so callers can uniformly pass the result to free().
    char* out = csvEscape("");
    TEST_ASSERT_NOT_NULL(out);
    TEST_ASSERT_EQUAL_STRING("", out);
    free(out);
}

void test_csv_escape_plain_string_is_returned_verbatim(void) {
    // No special chars — must NOT be wrapped in quotes (keeps the CSV
    // output identical to the legacy Arduino-String implementation).
    assert_escape_equals("hello", "hello");
    assert_escape_equals("kitchen", "kitchen");
    assert_escape_equals("My-Network-5G", "My-Network-5G");
}

void test_csv_escape_embedded_comma_wraps_in_quotes(void) {
    assert_escape_equals("a,b", "\"a,b\"");
    assert_escape_equals("east,west", "\"east,west\"");
}

void test_csv_escape_embedded_quote_doubles_inner_quote(void) {
    // Per RFC 4180, an embedded " inside a quoted field is escaped
    // by doubling it. So `say "hi"` becomes `"say ""hi"""`.
    assert_escape_equals("say \"hi\"", "\"say \"\"hi\"\"\"");
    assert_escape_equals("\"", "\"\"\"\"");
}

void test_csv_escape_embedded_newline_wraps_in_quotes(void) {
    // LF inside a field triggers quoting so downstream CSV readers
    // (including capture.py) treat the record as one logical row.
    assert_escape_equals("line1\nline2", "\"line1\nline2\"");
}

void test_csv_escape_embedded_cr_wraps_in_quotes(void) {
    // CR is also a CSV-record-breaking char on its own; must be quoted.
    assert_escape_equals("line1\rline2", "\"line1\rline2\"");
}

void test_csv_escape_mixed_special_chars_quotes_and_doubles(void) {
    // Combines comma + embedded quote + newline; exercises both the
    // wrap-in-quotes path AND the inner-quote-doubling path in one go.
    assert_escape_equals("a,b\"c\nd", "\"a,b\"\"c\nd\"");
}