/*
 * debounced_button.h
 * ------------------
 * Refactor of the global debounce state in src/main.cpp into a small
 * class driven by injected Clock / Pin references. This lets unit
 * tests drive the debounce logic with fakes (FakeClock / FakePin)
 * without a connected board.
 *
 * Behavior preserved from the original pollButtonPress /
 * waitForButtonRelease pair:
 *   - 50 ms (configurable) debounce window
 *   - HIGH -> LOW edge detection, fires once per stable transition
 *   - re-arms only after the pin returns HIGH
 */

#pragma once

#include "wifi_scan_util.h"

#include <Arduino.h>
#include <stdint.h>

class DebouncedButton {
public:
    DebouncedButton(Pin& pin, Clock& clock, uint32_t debounce_ms);

    // Returns true exactly once per HIGH->LOW transition once the pin
    // has been stably LOW for at least debounce_ms.
    bool pressed();

    // Blocks until the pin reads HIGH again, then resets the edge
    // detector so the next press can fire. Polls with delay(10).
    void wait_release();

private:
    Pin&         pin_;
    Clock&       clock_;
    uint32_t     debounce_ms_;
    int          last_raw_button_    = HIGH;
    int          last_stable_button_ = HIGH;
    uint32_t     last_debounce_ms_   = 0;
};
