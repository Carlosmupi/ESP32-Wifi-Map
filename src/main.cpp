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

#include <Arduino.h>
#include <WiFi.h>
#include <math.h>

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

// RSSI -> rough distance parameters (path-loss formula used by
// Wifi-Radar-Scanner-for-ESP32). Distance is capped so a very weak RSSI
// never produces a misleadingly large value.
constexpr int16_t  RSSI_REFERENCE_DBM   = -45;
constexpr float    DISTANCE_CAP_M       = 10.0f;
constexpr float    DISTANCE_SCALE       = 20.0f;

// Spot label and id state.
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

// Map Arduino-ESP32 wifi_auth_mode_t to a short stable token.
const char* authModeString(uint8_t auth) {
    switch (static_cast<wifi_auth_mode_t>(auth)) {
        case WIFI_AUTH_OPEN:             return "OPEN";
        case WIFI_AUTH_WEP:              return "WEP";
        case WIFI_AUTH_WPA_PSK:          return "WPA_PSK";
        case WIFI_AUTH_WPA2_PSK:         return "WPA2_PSK";
        case WIFI_AUTH_WPA_WPA2_PSK:     return "WPA_WPA2_PSK";
        case WIFI_AUTH_WPA2_ENTERPRISE:  return "WPA2_ENT";
        case WIFI_AUTH_WPA3_PSK:         return "WPA3_PSK";
        case WIFI_AUTH_WPA2_WPA3_PSK:    return "WPA2_WPA3_PSK";
        case WIFI_AUTH_WAPI_PSK:         return "WAPI_PSK";
        case WIFI_AUTH_WPA3_ENT_192:    return "WPA3_ENT_192";
        default:                        return "UNKNOWN";
    }
}

// CSV-escape a field per RFC 4180. Returns "" for an empty input.
String csvEscape(const String& field) {
    if (field.length() == 0) {
        return String("");
    }
    bool needs_quote = false;
    for (size_t i = 0; i < field.length(); ++i) {
        const char c = field.charAt(i);
        if (c == ',' || c == '"' || c == '\n' || c == '\r') {
            needs_quote = true;
            break;
        }
    }
    if (!needs_quote) {
        return field;
    }
    String out;
    out.reserve(field.length() + 2);
    out += '"';
    for (size_t i = 0; i < field.length(); ++i) {
        const char c = field.charAt(i);
        if (c == '"') {
            out += "\"\"";  // double the inner quote
        } else {
            out += c;
        }
    }
    out += '"';
    return out;
}

// RSSI -> rough distance in meters (capped).
inline float rssiToDistance(int32_t rssi_dbm) {
    float dist = exp(static_cast<float>(-rssi_dbm - RSSI_REFERENCE_DBM) /
                     DISTANCE_SCALE);
    if (dist > DISTANCE_CAP_M) dist = DISTANCE_CAP_M;
    if (dist < 0.0f)           dist = 0.0f;
    return dist;
}

// Truncate a label to fit the spot_label buffer (no embedded NULs to surprise).
void copyLabel(const String& src) {
    size_t n = src.length();
    if (n > SPOT_LABEL_MAX_LEN) n = SPOT_LABEL_MAX_LEN;
    for (size_t i = 0; i < n; ++i) {
        g_spot_label[i] = static_cast<char>(src.charAt(i));
    }
    g_spot_label[n] = '\0';
}

// Sample a non-empty serial line into g_spot_label. Returns true on update.
bool readSerialLabel() {
    if (!Serial.available()) {
        return false;
    }
    String line = Serial.readStringUntil('\n');
    line.trim();  // strips \r and whitespace
    if (line.length() == 0) {
        return false;
    }
    copyLabel(line);
    ledAckBlink();
    Serial.printf("# label=\"%s\"\n", g_spot_label);
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
            g_last_stable_button = raw;
            if (raw == LOW && g_last_stable_button == LOW) {
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
    const String  ssid_esc  = csvEscape(ssid);

    Serial.printf("%u,%s,%lu,%s,%s,%ld,%ld,%s,%.2f\n",
                  static_cast<unsigned>(spot_id),
                  spot_label,
                  static_cast<unsigned long>(ts_ms),
                  ssid_esc.c_str(),
                  bssid.c_str(),
                  static_cast<long>(rssi),
                  static_cast<long>(channel),
                  auth_str,
                  static_cast<double>(dist_m));
}

// Run one scan at the current spot and emit CSV rows to serial.
void logCurrentSpot() {
    ledOn();
    const uint32_t scan_start = millis();

    const int n = WiFi.scanNetworks(SCAN_ASYNC, SCAN_SHOW_HIDDEN,
                                   SCAN_PASSIVE,
                                   static_cast<int>(SCAN_MAX_MS_PER_CHAN),
                                   static_cast<int>(SCAN_CHANNEL));

    const uint16_t spot_id    = g_spot_id;
    const uint32_t stamp_ms   = millis();

    if (n <= 0) {
        Serial.printf("# spot=%u label=%s ap_count=0 scan_ms=%lu\n",
                      static_cast<unsigned>(spot_id),
                      g_spot_label,
                      static_cast<unsigned long>(millis() - scan_start));
        Serial.flush();
        ledOff();
        ++g_spot_id;
        ledConfirmBlinks();
        return;
    }

    for (int i = 0; i < n; ++i) {
        printApRow(spot_id, g_spot_label, stamp_ms, static_cast<uint8_t>(i));
    }
    Serial.flush();

    WiFi.scanDelete();

    Serial.printf("# spot=%u label=%s ap_count=%d scan_ms=%lu\n",
                  static_cast<unsigned>(spot_id),
                  g_spot_label,
                  n,
                  static_cast<unsigned long>(millis() - scan_start));
    Serial.flush();

    ledOff();
    ++g_spot_id;
    ledConfirmBlinks();
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
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    delay(100);

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
