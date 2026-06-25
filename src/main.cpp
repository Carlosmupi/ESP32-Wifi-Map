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
#include "esp_wifi.h"
#include "esp_wifi_types.h"
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
constexpr int      SCHEMA_VERSION       = 2;

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

// Runtime SSID ignore list (issue #23). In-memory only, lost on reboot.
// SSIDs are stored as NUL-terminated C strings up to 32 chars + NUL.
constexpr uint8_t MAX_IGNORED = 16;
constexpr uint8_t IGNORED_SSID_MAX_LEN = 32;
char     g_ignored_ssids[MAX_IGNORED][IGNORED_SSID_MAX_LEN + 1] = {{0}};
uint8_t  g_ignored_count = 0;

char        g_spot_label[SPOT_LABEL_MAX_LEN + 1] = "default";
uint16_t    g_spot_id                             = 0;

// Promiscuous-mode state (issue #1). True while the host has enabled
// probe-request sniffing via `!promisc on`. The sniffer callback and
// logCurrentSpot() both consult this flag so an active scan can briefly
// suspend promiscuous mode without losing the user's intent across scans.
bool        g_promisc_on      = false;
// True while logCurrentSpot() is mid-scan. Guards against re-entry from
// `!scan` or the `!monitor` background loop (issue #1's monitor-mode
// extension: a `!scan` triggered while the radio is already busy is
// silently dropped, and the monitor loop skips its tick).
bool        g_scan_in_flight  = false;
// Monitor mode (Phase 2 of the radio-monitor design). When `g_monitor_on`
// is true, loop() periodically calls logCurrentSpot() on the cadence
// given by g_monitor_interval_ms (gated by monitorTick from
// wifi_scan_util). The label is overwritten to "monitor" so monitor-
// mode rows are visually distinct in the captured CSV.
bool        g_monitor_on          = false;
uint32_t    g_monitor_interval_ms = 5000;
uint32_t    g_last_monitor_scan_ms = 0;
constexpr uint32_t MONITOR_MIN_INTERVAL_MS     = 1000;
constexpr uint32_t MONITOR_DEFAULT_INTERVAL_MS = 5000;


inline void ledOn()  { digitalWrite(LED_PIN, LED_ACTIVE_LOW ? LOW  : HIGH); }
inline void ledOff() { digitalWrite(LED_PIN, LED_ACTIVE_LOW ? HIGH : LOW ); }

// Forward declarations so handleCommand() can reference the promiscuous-
// mode helpers (issue #1) that are defined further down in this file.
void snifferCallback(void* buf, wifi_promiscuous_pkt_type_t type);
void setPromiscuousFilter();
// Forward-declared so handleCommand() (above) can call !scan -> logCurrentSpot().
void logCurrentSpot();

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
//   !ignore <ssid>   add an SSID to the runtime ignore list (issue #23)
//   !unignore <ssid> remove an SSID from the ignore list
//   !ignorelist      print the current ignore list
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

    if (strcmp(cmd, "!ignore") == 0) {
        // The remainder of the line after the command token is the SSID,
        // which may itself contain spaces. strtok already split on the
        // first whitespace, so the next token is the start of the SSID.
        char* ssid = strtok(nullptr, "");
        if (!ssid) {
            Serial.println("# cmd: missing ssid");
            Serial.flush();
            return true;
        }
        // Trim a leading space left over by strtok's empty-delimiter mode.
        while (*ssid == ' ' || *ssid == '\t') {
            ++ssid;
        }
        if (ssid[0] == '\0') {
            Serial.println("# cmd: missing ssid");
            Serial.flush();
            return true;
        }
        // Reject duplicates so the same SSID can't fill a slot twice.
        for (uint8_t i = 0; i < g_ignored_count; ++i) {
            if (strcmp(g_ignored_ssids[i], ssid) == 0) {
                Serial.printf("# ignored ssid=%s (already)\n", ssid);
                Serial.flush();
                return true;
            }
        }
        if (g_ignored_count >= MAX_IGNORED) {
            Serial.println("# cmd: ignore list full");
            Serial.flush();
            return true;
        }
        strncpy(g_ignored_ssids[g_ignored_count], ssid,
                IGNORED_SSID_MAX_LEN);
        g_ignored_ssids[g_ignored_count][IGNORED_SSID_MAX_LEN] = '\0';
        ++g_ignored_count;
        Serial.printf("# ignored ssid=%s (added)\n",
                      g_ignored_ssids[g_ignored_count - 1]);
        Serial.flush();
        return true;
    }

    if (strcmp(cmd, "!unignore") == 0) {
        char* ssid = strtok(nullptr, "");
        if (!ssid) {
            Serial.println("# cmd: missing ssid");
            Serial.flush();
            return true;
        }
        while (*ssid == ' ' || *ssid == '\t') {
            ++ssid;
        }
        if (ssid[0] == '\0') {
            Serial.println("# cmd: missing ssid");
            Serial.flush();
            return true;
        }
        for (uint8_t i = 0; i < g_ignored_count; ++i) {
            if (strcmp(g_ignored_ssids[i], ssid) == 0) {
                // Shift the tail down to keep the array dense.
                for (uint8_t j = i; j + 1 < g_ignored_count; ++j) {
                    strncpy(g_ignored_ssids[j], g_ignored_ssids[j + 1],
                            IGNORED_SSID_MAX_LEN + 1);
                }
                g_ignored_ssids[g_ignored_count - 1][0] = '\0';
                --g_ignored_count;
                Serial.printf("# unignored ssid=%s\n", ssid);
                Serial.flush();
                return true;
            }
        }
        Serial.printf("# unignored ssid=%s (not found)\n", ssid);
        Serial.flush();
        return true;
    }

    if (strcmp(cmd, "!ignorelist") == 0) {
        if (g_ignored_count == 0) {
            Serial.println("# ignorelist empty");
        } else {
            for (uint8_t i = 0; i < g_ignored_count; ++i) {
                Serial.printf("# ignorelist[%u]=%s\n",
                              static_cast<unsigned>(i),
                              g_ignored_ssids[i]);
            }
        }
        Serial.flush();
        return true;
    }

    if (strcmp(cmd, "!scan") == 0) {
        // Trigger a scan on demand. If a scan is already running, the
        // call is silently dropped to avoid re-entry (monitor mode).
        if (g_scan_in_flight) {
            Serial.println(F("# scan: busy"));
        } else {
            Serial.println(F("# scan: triggered"));
            logCurrentSpot();
        }
        Serial.flush();
        return true;
    }

    if (strcmp(cmd, "!monitor") == 0) {
        char* sub = strtok(nullptr, " \t");
        if (!sub) {
            Serial.println(F("# cmd: missing on/off"));
            Serial.flush();
            return true;
        }
        if (strcmp(sub, "on") == 0) {
            char* ms_arg = strtok(nullptr, " \t");
            uint32_t ms = MONITOR_DEFAULT_INTERVAL_MS;
            if (ms_arg) {
                const long v = strtol(ms_arg, nullptr, 10);
                if (v < static_cast<long>(MONITOR_MIN_INTERVAL_MS)) {
                    Serial.println(F("# monitor: out of range (min 1000)"));
                    Serial.flush();
                    return true;
                }
                ms = static_cast<uint32_t>(v);
            }
            g_monitor_interval_ms  = ms;
            g_last_monitor_scan_ms = millis();
            g_monitor_on           = true;
            // Synthetic label so the CSV clearly identifies monitor rows.
            copyLabel(g_spot_label, sizeof(g_spot_label), "monitor");
            Serial.printf("# monitor=on interval_ms=%u\n",
                          static_cast<unsigned>(ms));
        } else if (strcmp(sub, "off") == 0) {
            g_monitor_on = false;
            Serial.println(F("# monitor=off"));
        } else {
            Serial.println(F("# cmd: missing on/off"));
        }
        Serial.flush();
        return true;
    }

    if (strcmp(cmd, "!promisc") == 0) {
        char* arg = strtok(nullptr, " \t");
        if (!arg) {
            Serial.println("# cmd: missing on/off");
            Serial.flush();
            return true;
        }
        if (strcmp(arg, "on") == 0) {
            setPromiscuousFilter();
            esp_wifi_set_promiscuous_rx_cb(snifferCallback);
            esp_wifi_set_promiscuous(true);
            g_promisc_on = true;
            Serial.println("# promisc=on");
        } else if (strcmp(arg, "off") == 0) {
            esp_wifi_set_promiscuous(false);
            g_promisc_on = false;
            Serial.println("# promisc=off");
        } else {
            Serial.println("# cmd: missing on/off");
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

// Return true if `ssid` is in the runtime ignore list (issue #23).
bool isIgnored(const char* ssid) {
    for (uint8_t i = 0; i < g_ignored_count; ++i) {
        if (strcmp(g_ignored_ssids[i], ssid) == 0) {
            return true;
        }
    }
    return false;
}

// Sniffer callback for promiscuous-mode probe-request logging (issue #1).
// Registered by the `!promisc on` command. Only management frames are
// forwarded (the filter set in `setPromiscuousFilter()`), and we further
// narrow to probe requests (frame type 0, subtype 4) here. Each matching
// frame is emitted as a CSV row with frame_type="probe_req" and the
// client's source MAC in src_mac. SSID is parsed from the tagged
// parameters; a wildcard (null) probe yields an empty SSID.
//
// This runs in the Wi-Fi task context, so it must be fast and must not
// block. csvEscape() heap-allocates and free() releases it immediately;
// Serial.printf() is line-buffered by the Arduino core.
void snifferCallback(void* buf, wifi_promiscuous_pkt_type_t type) {
    if (type != WIFI_PKT_MGMT) {
        return;
    }
    if (!g_promisc_on) {
        return;
    }

    wifi_promiscuous_pkt_t* pkt = static_cast<wifi_promiscuous_pkt_t*>(buf);
    wifi_pkt_rx_ctrl_t ctrl = pkt->rx_ctrl;
    uint8_t* frame = pkt->payload;

    // 802.11 management frame: type=0 (bits 3:2 of byte 0), subtype=4
    // (bits 7:4 of byte 0) => probe request.
    uint8_t frame_type    = (frame[0] >> 2) & 0x03;
    uint8_t frame_subtype = (frame[0] >> 4) & 0x0F;
    if (frame_type != 0 || frame_subtype != 4) {
        return;
    }

    // Source MAC is address 2, bytes 10..15 of the management frame header.
    char src_mac[18];
    snprintf(src_mac, sizeof(src_mac), "%02x:%02x:%02x:%02x:%02x:%02x",
             frame[10], frame[11], frame[12], frame[13], frame[14],
             frame[15]);

    int16_t  rssi    = ctrl.rssi;
    uint8_t  channel = ctrl.channel;

    // Extract the SSID from the tagged parameters, which start at byte 24
    // of the management frame. Tag 0 is the SSID: 1 byte tag number,
    // 1 byte length, then the SSID bytes. A length of 0 means a wildcard
    // (broadcast) probe request with no specific SSID.
    char ssid_buf[33] = {0};
    const uint16_t payload_len = ctrl.sig_len;
    // The fixed management header is 24 bytes; tagged params follow. Guard
    // against short frames so we never read past the buffer.
    if (payload_len >= 26) {
        uint16_t pos = 24;
        while (pos + 2 <= payload_len) {
            uint8_t tag_num   = frame[pos];
            uint8_t tag_len   = frame[pos + 1];
            pos += 2;
            if (tag_num == 0) {
                // SSID tag. Cap at 32 bytes per the 802.11 spec.
                uint8_t copy_len = tag_len;
                if (copy_len > 32) {
                    copy_len = 32;
                }
                if (pos + copy_len <= payload_len) {
                    memcpy(ssid_buf, frame + pos, copy_len);
                    ssid_buf[copy_len] = '\0';
                }
                break;
            }
            pos += tag_len;
        }
    }

    const uint32_t ts_ms = millis();
    const float    dist_m = rssiToDistance(rssi);

    char* label_esc = csvEscape(g_spot_label);
    char* ssid_esc  = csvEscape(ssid_buf);
    char* mac_esc   = csvEscape(src_mac);

    Serial.printf("%u,%s,%lu,%s,,%ld,%u,,%.2f,probe_req,%s\n",
                  static_cast<unsigned>(g_spot_id),
                  label_esc,
                  static_cast<unsigned long>(ts_ms),
                  ssid_esc,
                  static_cast<long>(rssi),
                  static_cast<unsigned>(channel),
                  static_cast<double>(dist_m),
                  mac_esc);
    Serial.flush();

    free(label_esc);
    free(ssid_esc);
    free(mac_esc);
}

// Install the promiscuous-mode filter so only management frames reach the
// sniffer callback. Called once when enabling promiscuous mode.
void setPromiscuousFilter() {
    wifi_promiscuous_filter_t filter;
    filter.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT;
    esp_wifi_set_promiscuous_filter(&filter);
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

    Serial.printf("%u,%s,%lu,%s,%s,%ld,%ld,%s,%.2f,ap,\n",
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
    g_scan_in_flight = true;
    const uint32_t scan_start = millis();
    esp_task_wdt_reset();

    // Promiscuous mode and active scanning conflict on the ESP32 radio.
    // If the host has enabled promiscuous sniffing, suspend it for the
    // duration of WiFi.scanNetworks() and restore it afterwards so the
    // user's intent survives the scan (issue #1).
    const bool was_promisc = g_promisc_on;
    if (was_promisc) {
        esp_wifi_set_promiscuous(false);
    }

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
        if (was_promisc) {
            esp_wifi_set_promiscuous(true);
        }
        ledOff();
        ++g_spot_id;
        esp_task_wdt_reset();
        ledConfirmBlinks();
        g_scan_in_flight = false;
        return;
    }

    // Filter out ignored SSIDs (issue #23). Print a one-line notice the
    // first time each ignored SSID is encountered in this scan so the user
    // knows the filter fired, then skip the row. `ap_count` in the footer
    // reflects only non-ignored APs.
    int reported = 0;
    bool announced[MAX_IGNORED] = {false};
    for (int i = 0; i < n; ++i) {
        const String ssid = WiFi.SSID(i);
        if (isIgnored(ssid.c_str())) {
            // Find the matching ignore-list slot so we announce each
            // ignored SSID at most once per scan.
            for (uint8_t k = 0; k < g_ignored_count; ++k) {
                if (strcmp(g_ignored_ssids[k], ssid.c_str()) == 0) {
                    if (!announced[k]) {
                        char* ssid_esc = csvEscape(ssid.c_str());
                        Serial.printf("# ignored ssid=%s\n", ssid_esc);
                        free(ssid_esc);
                        announced[k] = true;
                    }
                    break;
                }
            }
            continue;
        }
        printApRow(spot_id, g_spot_label, stamp_ms, static_cast<uint8_t>(i));
        ++reported;
    }
    Serial.flush();

    WiFi.scanDelete();
    esp_task_wdt_reset();
    char* label_esc = csvEscape(g_spot_label);
    Serial.printf("# spot=%u label=%s ap_count=%d scan_ms=%lu\n",
                  static_cast<unsigned>(spot_id),
                  label_esc,
                  reported,
                  static_cast<unsigned long>(millis() - scan_start));
    free(label_esc);

    Serial.flush();

    if (was_promisc) {
        esp_wifi_set_promiscuous(true);
    }
    ledOff();
    ++g_spot_id;
    ledConfirmBlinks();
    esp_task_wdt_reset();
    g_scan_in_flight = false;
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
    Serial.println(F("# spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,auth_mode,est_distance_m,frame_type,src_mac"));
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

    esp_task_wdt_reset();
    delay(10);
}
