/*
 * Wi-Fi Scanner with Signal Map
 * -----------------------------
 * Bare-PCB Freenove ESP32 WROVER sketch.
 *
 *   1. User types a short label on the serial monitor (e.g. "kitchen").
 *      The LED blinks once to acknowledge.
 *   2. User presses the onboard BOOT button (GPIO0) at the current spot.
 *   3. The firmware runs a synchronous active scan across all 2.4 GHz
 *      channels and prints one CSV row per visible AP.
 *   4. Each row is tagged with the current spot_id and spot_label, plus a
 *      rough RSSI->distance estimate.
 *
 * Output (USB serial @ 115200 on COM10):
 *   # Wi-Fi Scanner with Signal Map
 *   # Freenove ESP32 WROVER | CH340 on COM10 @ 115200
 *   # spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,auth_mode,est_distance_m
 *   <spot_id>,<spot_label>,<millis()>,<ssid>,<bssid>,<rssi>,<channel>,<auth_mode>,<dist>
 *   # spot=<spot_id> label=<spot_label> ap_count=<n> scan_ms=<duration>
 *
 * Onboard LED on GPIO2 (active-low):
 *   - single short blink on label acknowledgement
 *   - solid while a scan is in flight
 *   - three rapid blinks after a successful scan
 */

#include "wifi_scan_util.h"

#include <Arduino.h>
#include <WiFi.h>
#include <math.h>
#include <esp_task_wdt.h>

namespace {

// Onboard LED on the Freenove WROVER is wired to GPIO2 and is active-low.
constexpr uint8_t  LED_PIN              = 2;
constexpr bool     LED_ACTIVE_LOW       = true;

// BOOT button on GPIO0 is active-low; we drive the internal pull-up.
constexpr uint8_t  BUTTON_PIN           = 0;
constexpr uint32_t BUTTON_DEBOUNCE_MS   = 50UL;

// Spot label length cap (kept short so a row fits in one terminal line).
constexpr uint8_t  SPOT_LABEL_MAX_LEN   = 31;

// Per-channel dwell parameters for WiFi.scanNetworks().
constexpr bool     SCAN_ASYNC           = false;
constexpr bool     SCAN_SHOW_HIDDEN     = true;
constexpr bool     SCAN_PASSIVE         = false;
constexpr uint16_t SCAN_MAX_MS_PER_CHAN = 300;
constexpr uint8_t  SCAN_CHANNEL         = 0;  // 0 = scan every 2.4 GHz channel.

char        g_spot_label[SPOT_LABEL_MAX_LEN + 1] = "default";
uint16_t    g_spot_id                             = 0;

// Button debounce state.
int         g_last_raw_button      = HIGH;
int         g_last_stable_button   = HIGH;
uint32_t    g_last_debounce_ms     = 0;

inline void ledOn()  { digitalWrite(LED_PIN, LED_ACTIVE_LOW ? LOW  : HIGH); }
inline void ledOff() { digitalWrite(LED_PIN, LED_ACTIVE_LOW ? HIGH : LOW ); }

// Brief single blink (100 ms on / 100 ms off) for label acknowledgement.
inline void ledAckBlink() {
    ledOn();
    delay(100);
    ledOff();
    delay(100);
}

// Three rapid blinks to signal a successful scan.
inline void ledConfirmBlinks() {
    for (uint8_t i = 0; i < 3; ++i) {
        ledOn();
        delay(100);
        ledOff();
        delay(100);
    }
}

// Sample a non-empty serial line into g_spot_label. Returns true on update.
bool readSerialLabel() {
    if (!Serial.available()) {
        return false;
    }
    // Bounded read into a stack buffer so a multi-megabyte paste cannot
    // allocate a proportionally large Arduino String and OOM-crash the board.
    // +1 for NUL terminator, +1 as a sentinel headroom past the label cap.
    char buf[SPOT_LABEL_MAX_LEN + 2];
    const size_t n = Serial.readBytesUntil('\n', buf, sizeof(buf) - 1);
    buf[n] = '\0';
    // Trim a trailing '\r' so CRLF serial-monitor input still works.
    if (n > 0 && buf[n - 1] == '\r') {
        buf[n - 1] = '\0';
    }
    // Discard any overflow bytes until the next '\n' (or end of stream)
    // so a giant pasted line is consumed without being stored.
    while (Serial.available() && Serial.peek() != '\n') {
        Serial.read();
    }
    if (Serial.available() && Serial.peek() == '\n') {
        Serial.read();
    }
    if (buf[0] == '\0') {
        return false;
    }
    copyLabel(g_spot_label, sizeof(g_spot_label), buf);
    ledAckBlink();
    char* label_esc = csvEscape(g_spot_label);
    Serial.printf("# label=%s\n", label_esc);
    free(label_esc);
    Serial.flush();
    return true;
}

// Debounced button-edge detector. Returns true once per HIGH->LOW transition.
bool pollButtonPress() {
    const uint32_t now_ms = millis();
    const int raw = digitalRead(BUTTON_PIN);

    if (raw != g_last_raw_button) {
        g_last_raw_button   = raw;
        g_last_debounce_ms  = now_ms;
    }

    if ((now_ms - g_last_debounce_ms) >= BUTTON_DEBOUNCE_MS) {
        if (raw != g_last_stable_button) {
            const bool was_high = (g_last_stable_button == HIGH);
            g_last_stable_button = raw;
            if (raw == LOW && was_high) {
                // Wait until the button is released before arming another press.
                return true;
            }
        }
    }
    return false;
}

// Wait until the button is released so we don't re-trigger on one hold.
void waitForButtonRelease() {
    while (digitalRead(BUTTON_PIN) == LOW) {
        delay(10);
    }
    g_last_raw_button    = HIGH;
    g_last_stable_button = HIGH;
}

// Print one CSV row for a single AP. Performs SSID escaping and distance.
void printApRow(uint16_t spot_id, const char* spot_label, uint32_t ts_ms,
                uint8_t index) {
    const String  ssid      = WiFi.SSID(index);
    const String  bssid     = WiFi.BSSIDstr(index);
    const int32_t rssi      = WiFi.RSSI(index);
    const int32_t channel   = WiFi.channel(index);
    const uint8_t auth      = WiFi.encryptionType(index);
    const char*   auth_str  = authModeString(auth);
    const float   dist_m    = rssiToDistance(rssi);
    char*         label_esc = csvEscape(spot_label);
    char*         ssid_esc  = csvEscape(ssid.c_str());

    Serial.printf("%u,%s,%lu,%s,%s,%ld,%ld,%s,%.2f\n",
                  static_cast<unsigned>(spot_id),
                  label_esc,
                  static_cast<unsigned long>(ts_ms),
                  ssid_esc,
                  bssid.c_str(),
                  static_cast<long>(rssi),
                  static_cast<long>(channel),
                  auth_str,
                  static_cast<double>(dist_m));
    free(label_esc);
    free(ssid_esc);

}

// Run one scan at the current spot and emit CSV rows to serial.
void logCurrentSpot() {
    ledOn();
    const uint32_t scan_start = millis();
    esp_task_wdt_reset();

    const int n = WiFi.scanNetworks(SCAN_ASYNC, SCAN_SHOW_HIDDEN,
                                   SCAN_PASSIVE,
                                   static_cast<int>(SCAN_MAX_MS_PER_CHAN),
                                   static_cast<int>(SCAN_CHANNEL));

    const uint16_t spot_id    = g_spot_id;
    const uint32_t stamp_ms   = millis();

    if (n <= 0) {
        char* label_esc = csvEscape(g_spot_label);
        Serial.printf("# spot=%u label=%s ap_count=0 scan_ms=%lu\n",
                      static_cast<unsigned>(spot_id),
                      label_esc,
                      static_cast<unsigned long>(millis() - scan_start));
        free(label_esc);
        Serial.flush();
        ledOff();
        ++g_spot_id;
        esp_task_wdt_reset();
        ledConfirmBlinks();
        return;
    }

    for (int i = 0; i < n; ++i) {
        printApRow(spot_id, g_spot_label, stamp_ms, static_cast<uint8_t>(i));
    }
    Serial.flush();

    WiFi.scanDelete();
    esp_task_wdt_reset();
    char* label_esc = csvEscape(g_spot_label);
    Serial.printf("# spot=%u label=%s ap_count=%d scan_ms=%lu\n",
                  static_cast<unsigned>(spot_id),
                  label_esc,
                  n,
                  static_cast<unsigned long>(millis() - scan_start));
    free(label_esc);

    Serial.flush();

    ledOff();
    ++g_spot_id;
    ledConfirmBlinks();
    esp_task_wdt_reset();
}

}  // namespace

void setup() {
    Serial.begin(115200);
    // Give the host time to attach a monitor after reset.
    delay(200);
    Serial.println();
    Serial.println(F("# Wi-Fi Scanner with Signal Map"));
    Serial.println(F("# Freenove ESP32 WROVER | CH340 on COM10 @ 115200"));
    Serial.println(F("# spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,auth_mode,est_distance_m"));
    Serial.flush();

    pinMode(LED_PIN, OUTPUT);
    ledOff();

    pinMode(BUTTON_PIN, INPUT_PULLUP);

    // Station mode is required for scanNetworks().
    WiFi.disconnect();
    delay(100);

    // 10 s panic timeout — recovers the board if WiFi.scanNetworks() hangs.
    esp_task_wdt_init(10, true);
    esp_task_wdt_add(NULL);

    // Initial ack so the user knows the board is alive.
    ledAckBlink();
    Serial.println(F("# ready: type a label and press Enter, then BOOT to log a spot"));
    Serial.flush();
}

void loop() {
    readSerialLabel();

    if (pollButtonPress()) {
        logCurrentSpot();
        waitForButtonRelease();
    }

    delay(10);
}
