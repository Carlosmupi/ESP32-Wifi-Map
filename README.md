# Wi-Fi Scanner with Signal Mapping — Idea Document

> Bare-PCB project: walk around with the ESP32 and record Wi-Fi signal strength per location.

## Concept

The ESP32 scans for nearby access points, records each network's **SSID**, **BSSID**, **RSSI**, **channel**, and **encryption type**, and tags the reading with a location marker. A human presses the onboard BOOT button each time they stand at a new spot. Later, the data is plotted as a 2-D signal-strength heatmap.

## What You Get

- A map of which rooms have good/bad Wi-Fi coverage.
- A list of hidden or unexpected access points.
- A fun way to visualize radio propagation in your space.

## Hardware Needed

Just the Freenove ESP32 WROVER PCB:

- Onboard LED to show scan status.
- BOOT button (`GPIO0`) as a "log this spot" trigger.
- USB serial to dump CSV data to a laptop.

No external components required.

## Data Collected

```csv
spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,auth_mode
0,kitchen,12345,HomeNet,AA:BB:CC:DD:EE:FF,-42,6,WPA2
0,kitchen,12346,Neighbor,11:22:33:44:55:66,-67,1,WPA2
1,bedroom,12400,HomeNet,AA:BB:CC:DD:EE:FF,-55,6,WPA2
```

## How to Use It

1. Upload the sketch.
2. Open the serial monitor.
3. Walk to a location, type a short label like `kitchen`, press BOOT to log a spot.
4. Walk to the next location, type `bedroom`, press BOOT again.
5. Copy the CSV from the serial monitor.
6. Plot with Python/Matplotlib or any spreadsheet.

## Visualization Ideas

- **Per-AP heatmap**: one colored map per SSID showing RSSI per spot.
- **Coverage comparison**: overlay multiple networks on the same floor plan.
- **Channel map**: show which channels are used by which APs at each spot.

## Algorithm Sketch

```text
loop:
    if serial label received:
        store label as current spot
        blink LED
    if BOOT button pressed:
        WiFi.scanNetworks()
        for each network found:
            print CSV row: spot, label, millis(), SSID, BSSID, RSSI, channel, encryption
        blink LED rapidly to confirm
```

## Key APIs / Libraries

- `WiFi.scanNetworks()` — scan nearby APs.
- `WiFi.SSID(i)`, `WiFi.BSSIDstr(i)`, `WiFi.RSSI(i)`, `WiFi.channel(i)`, `WiFi.encryptionType(i)` — read scan results.
- `touchRead()` or `digitalRead(GPIO0)` — detect BOOT button press.
- Onboard LED on `GPIO2` for feedback.

## Related Kit Examples

- `C/Sketches/Sketch_24.1_WiFi_Station/` — basic Wi-Fi connection.
- `C/Sketches/Sketch_02.1_ButtonAndLed/` — button + LED pattern.

## Next Steps (If You Want to Build It)

1. Define the CSV format precisely.
2. Decide how spots are labeled (serial input vs. pre-defined list).
3. Add RTC timestamp if you want wall-clock time.
4. Write a Python script to parse CSV and generate heatmaps.
5. Optional: store readings in SPIFFS/flash so the board works untethered.

---

*Idea generated for the Freenove ESP32 WROVER. Do not implement without a design review.*
