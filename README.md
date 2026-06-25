# Wi-Fi Scanner with Signal Map

> Bare-PCB project for the Freenove ESP32 WROVER: walk around your space, press BOOT at each spot, and get a per-AP signal-strength map.

## CI

[![CI](https://github.com/Carlosmupi/ESP32-Wifi-Map/actions/workflows/ci.yml/badge.svg)](https://github.com/Carlosmupi/ESP32-Wifi-Map/actions/workflows/ci.yml)

## Concept

The ESP32 scans every nearby access point across all 2.4 GHz channels, tags each reading with the current spot label and an auto-incrementing spot ID, and streams the result as CSV over USB serial. A Python script captures the live CSV to disk; a second script renders per-SSID signal-strength heatmaps. The `est_distance_m` column is a rough path-loss estimate (`exp((-RSSI - 45) / 20)` capped at 10 m) — useful for relative comparison, not absolute distance.

## Hardware Needed

Just the Freenove ESP32 WROVER PCB:

* Onboard LED on **GPIO2** (active-low) shows scan status.
* BOOT button on **GPIO0** (active-low, internal pull-up) triggers a scan.
* USB serial (CH340) prints CSV at 115200 baud on **COM10**.

No external components required.

## CSV Format

Boot banner (printed once at startup):

```csv
# Wi-Fi Scanner with Signal Map
# Freenove ESP32 WROVER | CH340 on COM10 @ 115200
# fw_version=0.2.0
# mac=AA:BB:CC:DD:EE:FF
# schema_version=2
# spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,auth_mode,est_distance_m,frame_type,src_mac
```

Data rows (one per visible AP at each spot):

```csv
0,kitchen,12345,HomeNet,AA:BB:CC:DD:EE:FF,-42,6,WPA2_PSK,1.58,ap,
0,kitchen,12346,Neighbor,11:22:33:44:55:66,-67,1,WPA2_PSK,5.92,ap,
1,bedroom,12400,HomeNet,AA:BB:CC:DD:EE:FF,-55,6,WPA2_PSK,3.16,ap,
# spot=0 label=kitchen ap_count=2 scan_ms=1820
# spot=1 label=bedroom ap_count=1 scan_ms=1750
```

With promiscuous mode on, probe-request rows are interleaved:

```csv
0,kitchen,34000,,00:11:22:33:44:55,-71,6,,,3.16,probe_req,aa:bb:cc:dd:ee:ff
```

| Column | Meaning |
| --- | --- |
| `spot_id` | Auto-increment, starts at 0, incremented once per successful scan. |
| `spot_label` | Free-text label set via USB serial (max 31 chars). |
| `timestamp_ms` | `millis()` at scan completion (AP rows) or frame reception (probe rows). |
| `ssid` | Network name. CSV-escaped; cells starting with `=`, `+`, `-`, `@` are prefixed with `'` to prevent spreadsheet formula injection. |
| `bssid` | MAC address, uppercase hex with colons. Empty for probe-request rows. |
| `rssi` | Signal strength in dBm (negative; less negative = stronger). |
| `channel` | 2.4 GHz channel (1-13). |
| `auth_mode` | One of `OPEN`, `WEP`, `WPA_PSK`, `WPA2_PSK`, `WPA_WPA2_PSK`, `WPA2_ENT`, `WPA3_PSK`, `WPA2_WPA3_PSK`, `WAPI_PSK`, `WPA3_ENT_192`, `UNKNOWN`. Empty for probe-request rows. |
| `est_distance_m` | `exp((-rssi - 45) / 20)`, capped at 10.00 m, two decimals. |
| `frame_type` | `ap` for access-point scan rows, `probe_req` for promiscuous-mode probe-request captures. |
| `src_mac` | Source MAC of the probing client (probe-request rows only; empty for AP rows). |

Lines starting with `#` are comments used by `capture.py` to detect the header, schema version, and spot boundaries.

## Build, Upload, and Monitor

```bash
pio run
pio run --target upload
pio device monitor --port COM10 --baud 115200
```

Replace `COM10` with your board's port (e.g., `/dev/ttyUSB0` on Linux).

## Workflow

1. Connect the board via USB.
2. Build and upload the firmware (see above).
3. Open the serial monitor.
4. Walk to a spot, type a short label like `kitchen` and press Enter. The LED blinks once to acknowledge.
5. Press the BOOT button. The LED stays on during the scan, then rapid-blinks 3 times when complete.
6. Walk to the next spot, type a new label, press BOOT again. Repeat for every spot you want to measure.
7. Stop the monitor and run `capture.py` to dump the data to disk (next section).

To capture and plot in one session, run `capture.py` first (it owns the serial port), then press BOOT while it is running.

## Runtime Commands

Type any of these in the serial monitor (or send them via `capture.py`) alongside spot labels:

| Command | Effect |
| --- | --- |
| `!dwell <ms>` | Set per-channel scan dwell (50-2000 ms; default 300). |
| `!channel <0\|1-14>` | Set scan channel (0 = all; default 0). |
| `!ignore <ssid>` | Add an SSID to the runtime ignore list (filtered from scan output; in-memory, lost on reboot). |
| `!unignore <ssid>` | Remove an SSID from the ignore list. |
| `!ignorelist` | Print the current ignore list. |
| `!promisc on` / `!promisc off` | Enable/disable promiscuous-mode probe-request logging. |

Unknown `!` commands echo `# unknown cmd`.

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

# Filter to specific SSIDs only:
python heatmap.py logs/signal_map_YYYYMMDD_HHMMSS.csv --ssid HomeNet --ssid Neighbor

# Single combined figure with a grid of subplots (up to 9 SSIDs by default):
python heatmap.py logs/signal_map_YYYYMMDD_HHMMSS.csv --combined
python heatmap.py logs/signal_map_YYYYMMDD_HHMMSS.csv --combined --combined-max 16
```

`coords.csv` schema:

```csv
spot_label,x,y
kitchen,0,0
bedroom,5,0
livingroom,2,3
```

PNG files are written next to the input CSV: `<basename>_<SSID>_heatmap.png` (with `--coords`) or `<basename>_<SSID>_bars.png` (without). With `--combined`, a single `<basename>_combined.png` is written instead.

### Merging multiple capture sessions

```bash
python -m wifiscan.merge logs/signal_map_20260625_100000.csv logs/signal_map_20260625_140000.csv
# Custom output path:
python -m wifiscan.merge logs/a.csv logs/b.csv --output logs/merged.csv
# Deduplicate identical (spot_label, bssid, channel) triplets by median RSSI:
python -m wifiscan.merge logs/a.csv logs/b.csv --dedup median
```

The merged CSV has globally-unique `spot_id` values (offset per input) and can be fed directly into `heatmap.py`.

## Notes

* `est_distance_m` is a rough estimate based on a free-space path-loss model with a reference of `-45 dBm` at 1 m. Real-world signal propagation varies with walls, reflections, and antenna orientation -- use it for relative comparison between spots, not as ground truth.
* The ESP32 can only listen on one channel at a time; full-band scans take roughly `13 x dwell_ms` (about 4 s at the default 300 ms/channel). Use `!dwell` to trade accuracy for speed.
* Promiscuous-mode probe-request logging (`!promisc on`) captures nearby client devices' probe frames. It is automatically suspended during active scans and resumes afterward. Probe rows have `frame_type=probe_req` and a `src_mac` field.
