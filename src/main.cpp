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
#include "debounced_button.h"

#include <Arduino.h>
#include <WiFi.h>
#include <math.h>
#include <esp_task_wdt.h>
#define FIRMWARE_VERSION "0.2.0"

namespace {

// Onboard LED on the Freenove WROVER is wired to GPIO2 and is active-low.
constexpr uint8_t  LED_PIN              = 2;
constexpr bool     LED_ACTIVE_LOW       = true;

// BOOT button on GPIO0 is active-low; we drive the internal pull-up.
constexpr uint8_t  BUTTON_PIN           = 0;
constexpr uint32_t BUTTON_DEBOUNCE_MS   = 50UL;

// CSV schema version advertised at boot as "# schema_version=N",
// immediately before the column header line. Must match
// wifiscan.schema.SCHEMA_VERSION; verified by tools/check_schema.py.
constexpr int      SCHEMA_VERSION       = 1;

// Spot label length cap (kept short so a row fits in one terminal line).
constexpr uint8_t  SPOT_LABEL_MAX_LEN   = 31;

// Per-channel dwell parameters for WiFi.scanNetworks().
constexpr bool     SCAN_ASYNC           = false;
constexpr bool     SCAN_SHOW_HIDDEN     = true;
constexpr bool     SCAN_PASSIVE         = false;

// Runtime-tunable scan parameters (issue #22). Initialized to the former
// compile-time defaults so behavior is unchanged until the host sends a
// !dwell or !channel command.
constexpr uint16_t DWELL_DEFAULT_MS     = 300;
constexpr uint8_t  CHANNEL_DEFAULT      = 0;  // 0 = scan every 2.4 GHz channel.
constexpr uint16_t DWELL_MIN_MS         = 50;
constexpr uint16_t DWELL_MAX_MS         = 2000;
constexpr uint8_t  CHANNEL_MAX          = 14;  // 2.4 GHz top channel.

uint16_t    g_dwell_ms  = DWELL_DEFAULT_MS;
uint8_t     g_channel   = CHANNEL_DEFAULT;

char        g_spot_label[SPOT_LABEL_MAX_LEN + 1] = "default";
uint16_t    g_spot_id                             = 0;


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

// Handle a `!`-prefixed host command (issue #22). Returns true if the line
// was a command (consumed), false otherwise. Commands:
//   !dwell <ms>      set per-channel scan dwell (50-2000 ms)
//   !channel <n>     set scan channel (0 = all, 1..14 = single 2.4 GHz chan)
// Any other `!`-prefixed line echoes an unknown-command notice.
bool handleCommand(char* line) {
    if (line[0] != '!') {
        return false;
    }

    // Tokenize on whitespace. strtok is safe here because `line` is a
    // mutable stack buffer owned by readSerialLabel().
    char* cmd = strtok(line, " \t");
    if (!cmd) {
        Serial.println("# unknown cmd: !");
        Serial.flush();
        return true;
    }

    if (strcmp(cmd, "!dwell") == 0) {
        char* arg = strtok(nullptr, " \t");
        if (!arg) {
            Serial.println("# cmd: out of range");
            Serial.flush();
            return true;
        }
        const long v = strtol(arg, nullptr, 10);
        if (v < static_cast<long>(DWELL_MIN_MS) ||
            v > static_cast<long>(DWELL_MAX_MS)) {
            Serial.println("# cmd: out of range");
        } else {
            g_dwell_ms = static_cast<uint16_t>(v);
            Serial.printf("# dwell=%u\n", static_cast<unsigned>(g_dwell_ms));
        }
        Serial.flush();
        return true;
    }

    if (strcmp(cmd, "!channel") == 0) {
        char* arg = strtok(nullptr, " \t");
        if (!arg) {
            Serial.println("# cmd: out of range");
            Serial.flush();
            return true;
        }
        const long v = strtol(arg, nullptr, 10);
        if (v < 0 || v > static_cast<long>(CHANNEL_MAX)) {
            Serial.println("# cmd: out of range");
        } else {
            g_channel = static_cast<uint8_t>(v);
            Serial.printf("# channel=%u\n", static_cast<unsigned>(g_channel));
        }
        Serial.flush();
        return true;
    }

    // Unknown command: echo the original token so the user sees what was
    // rejected. Re-print from `cmd` (already NUL-terminated by strtok).
    Serial.printf("# unknown cmd: %s\n", cmd);
    Serial.flush();
    return true;
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
    // `!`-prefixed lines are host commands, not spot labels (issue #22).
    if (handleCommand(buf)) {
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
                                   static_cast<int>(g_dwell_ms),
                                   static_cast<int>(g_channel));

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
    Serial.printf("# fw_version=%s\n", FIRMWARE_VERSION);
    Serial.printf("# mac=%s\n", WiFi.macAddress().c_str());
    Serial.printf("# schema_version=%d\n", SCHEMA_VERSION);
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
    // Static locals keep the button state across loop iterations and
    // give the references a stable address so DebouncedButton can hold
    // Clock& / Pin& without dangling.
    static ArduinoClock     clock;
    static ArduinoPin       button_pin(BUTTON_PIN);
    static DebouncedButton  boot(button_pin, clock, BUTTON_DEBOUNCE_MS);

    readSerialLabel();

    if (boot.pressed()) {
        logCurrentSpot();
        boot.wait_release();
    }

    delay(10);
}
