/*
 * io_abstractions.cpp
 * -------------------
 * Arduino-coupled bodies for the Clock / Pin adapters declared in
 * src/wifi_scan_util.h. Keeping them in their own translation unit
 * isolates the <Arduino.h> dependency from the framework-free helper
 * file, so anything that only needs the interface types (unit tests,
 * pure-logic code) can include the header without dragging in the
 * framework.
 */

#include "wifi_scan_util.h"

#include <Arduino.h>

uint32_t ArduinoClock::now_ms() {
    return millis();
}

ArduinoPin::ArduinoPin(uint8_t pin) : pin_(pin) {}

int ArduinoPin::read() {
    return digitalRead(pin_);
}
