# Wi-Fi Scanner with Signal Map

> Bare-PCB project for the Freenove ESP32 WROVER: walk around your space, press BOOT at each spot, and get a per-AP signal-strength map.

## Concept

The ESP32 scans every nearby access point across all 2.4 GHz channels, tags each reading with the current spot label and an auto-incrementing spot ID, and streams the result as CSV over USB serial. A Python script captures the live CSV to disk; a second script renders per-SSID signal-strength heatmaps. The `est_distance_m` column is a rough path-loss estimate (`exp((-RSSI - 45) / 20)` capped at 10 m) — useful for relative comparison, not absolute distance.

## Hardware Needed

Just the Freenove ESP32 WROVER PCB:

* Onboard LED on **GPIO2** (active-low) shows scan status.
* BOOT button on **GPIO0** (active-low, internal pull-up) triggers a scan.
* USB serial (CH340) prints CSV at 115200 baud on **COM10**.

No external components required.

## CSV Format

Header (printed once at boot):

```csv
# Wi-Fi Scanner with Signal Map
# Freenove ESP32 WROVER | CH340 on COM10 @ 115200
# spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,auth_mode,est_distance_m
```

Data rows (one per visible AP at each spot):

```csv
0,kitchen,12345,HomeNet,AA:BB:CC:DD:EE:FF,-42,6,WPA2_PSK,1.58
0,kitchen,12346,Neighbor,11:22:33:44:55:66,-67,1,WPA2_PSK,5.92
1,bedroom,12400,HomeNet,AA:BB:CC:DD:EE:FF,-55,6,WPA2_PSK,3.16
# spot=0 label=kitchen ap_count=2 scan_ms=1820
# spot=1 label=bedroom ap_count=1 scan_ms=1750
```

| Column | Meaning |
| --- | --- |
| `spot_id` | Auto-increment, starts at 0, incremented once per successful scan. |
| `spot_label` | Free-text label set via USB serial (max 31 chars). |
| `timestamp_ms` | `millis()` at scan completion. |
| `ssid` | Network name (CSV-escaped if it contains `,` or `"`). |
| `bssid` | MAC address, uppercase hex with colons. |
| `rssi` | Signal strength in dBm (negative; less negative = stronger). |
| `channel` | 2.4 GHz channel (1–13). |
| `auth_mode` | One of `OPEN`, `WEP`, `WPA_PSK`, `WPA2_PSK`, `WPA_WPA2_PSK`, `WPA2_ENT`, `WPA3_PSK`, `WPA2_WPA3_PSK`, `WAPI_PSK`, `WPA3_ENT_192`, `UNKNOWN`. |
| `est_distance_m` | `exp((-rssi - 45) / 20)`, capped at 10.00 m, two decimals. |

The lines starting with `#` are comments used by `capture.py` to detect spot boundaries.

## Build, Upload, and Monitor

```bash
pio run
pio run --target upload
pio device monitor --port COM10 --baud 115200
```

## Workflow

1. Connect the board via USB.
2. Build and upload the firmware (see above).
3. Open the serial monitor.
4. Walk to a spot, type a short label like `kitchen` and press Enter. The LED blinks once to acknowledge.
5. Press the BOOT button. The LED stays on during the scan, then rapid-blinks 3 times when complete.
6. Walk to the next spot, type a new label, press BOOT again. Repeat for every spot you want to measure.
7. Stop the monitor and run `capture.py` to dump the data to disk (next section).

To capture and plot in one session, run `capture.py` first (it owns the serial port), then press BOOT while it is running.

## Python Tools

Requirements:

```bash
pip install pyserial pandas matplotlib
```

Capture live serial CSV to `logs/signal_map_YYYYMMDD_HHMMSS.csv`:

```bash
python capture.py
# or override the port:
python capture.py --port COM11
```

`capture.py` echoes every line, buffers CSV rows until the firmware's spot footer comment, then writes the buffer to a timestamped file under `logs/`. Press **Ctrl+C** to stop; any partial buffer is flushed on exit.

Render per-SSID heatmaps:

```bash
# Without coordinates: bar charts of RSSI per spot.
python heatmap.py logs/signal_map_YYYYMMDD_HHMMSS.csv

# With coordinates: 2-D scatter heatmap (annotated with spot label + est_distance_m).
python heatmap.py logs/signal_map_YYYYMMDD_HHMMSS.csv --coords coords.csv
```

`coords.csv` schema:

```csv
spot_label,x,y
kitchen,0,0
bedroom,5,0
livingroom,2,3
```

PNG files are written to the same `logs/` directory: `<basename>_<SSID>_heatmap.png` (with `--coords`) or `<basename>_<SSID>_bars.png` (without).

## Notes

* `est_distance_m` is a rough estimate based on a free-space path-loss model with a reference of `-45 dBm` at 1 m. Real-world signal propagation varies with walls, reflections, and antenna orientation — use it for relative comparison between spots, not as ground truth.
* The ESP32 can only listen on one channel at a time; full-band scans take roughly `13 × SCAN_MAX_MS_PER_CHAN` (≈ 4 s at the default 300 ms/channel).
* This project uses active `WiFi.scanNetworks()` only — it enumerates APs but not client stations. Promiscuous-mode capture (which would also log nearby devices' probe traffic) is a known future extension; see the plan's "Promiscuous mode" note.

## Related Kit Examples

* `C/Sketches/Sketch_02.1_ButtonAndLed/` — button + LED pattern.
* `C/Sketches/Sketch_24.1_WiFi_Station/` — basic Wi-Fi API usage.
