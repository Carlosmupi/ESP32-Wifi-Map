/*
 * debounced_button.cpp
 * --------------------
 * Body of DebouncedButton. Mirrors the prior globals in src/main.cpp
 * but keeps all state inside the class and reads time / pin through the
 * injected Clock / Pin interfaces so tests can substitute fakes.
 *
 * wait_release() still calls Arduino's delay(10) so a held button does
 * not busy-loop the CPU on real hardware. Unit tests that need to drive
 * wait_release() must stub delay() and flip their FakePin to HIGH.
 */

#include "debounced_button.h"

#include <Arduino.h>

DebouncedButton::DebouncedButton(Pin& pin, Clock& clock, uint32_t debounce_ms)
    : pin_(pin), clock_(clock), debounce_ms_(debounce_ms) {}

bool DebouncedButton::pressed() {
    const uint32_t now_ms = clock_.now_ms();
    const int      raw    = pin_.read();

    if (raw != last_raw_button_) {
        last_raw_button_  = raw;
        last_debounce_ms_ = now_ms;
    }

    if ((now_ms - last_debounce_ms_) >= debounce_ms_) {
        if (raw != last_stable_button_) {
            const bool was_high = (last_stable_button_ == HIGH);
            last_stable_button_ = raw;
            if (raw == LOW && was_high) {
                // Wait until the button is released before arming another press.
                return true;
            }
        }
    }
    return false;
}

void DebouncedButton::wait_release() {
    while (pin_.read() == LOW) {
        delay(10);
    }
    last_raw_button_    = HIGH;
    last_stable_button_ = HIGH;
}
