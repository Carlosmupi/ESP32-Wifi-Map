# AGENTS.md — wifi-scanner-signal-map

Project-specific notes for Codex. Read the parent `C:\Dev\Embedded\AGENTS.md` and `C:\Dev\Embedded\projects\AGENTS.md` first.

## Hardware

* Board: Freenove ESP32 WROVER (ESP32-D0WD-V3, 4 MB flash, 8 MB PSRAM).
* USB-UART: CH340 on **COM10** at **115200 baud**.
* Onboard LED: **GPIO2**, active-low (`LOW` = on, `HIGH` = off).
* Onboard button: BOOT on **GPIO0**, active-low (`LOW` = pressed). Uses `INPUT_PULLUP`.
* No external components.

## Pinout

| Pin | Role | Notes |
| --- | --- | --- |
| GPIO2 | Status LED | Active-low, internal drive. |
| GPIO0 | BOOT button | Active-low, `INPUT_PULLUP`. Triggers a scan on stable HIGH→LOW edge. |

## Build, Upload, Monitor

```powershell
$env:PLATFORMIO_CORE_DIR = 'C:\Dev\Embedded\pio-core'
$env:PATH = 'C:\Dev\Embedded\pio-core\penv\Scripts;' + $env:PATH

pio run                       # compile
pio run --target upload      # flash to COM10
pio device monitor --port COM10 --baud 115200
```

## Workflow

1. `pio run --target upload`.
2. Open the serial monitor.
3. Type a label (e.g. `kitchen`) and press Enter; LED blinks once.
4. Press BOOT to log a scan; LED stays on during scan, then 3 rapid blinks.
5. Repeat for each spot.
6. Close the monitor; run `python capture.py` to dump rows to `logs/signal_map_*.csv`.
7. Run `python heatmap.py <csv>` (optionally with `--coords coords.csv`) to render plots.

## Files

| File | Role |
| --- | --- |
| `platformio.ini` | Board/port/baud/flash config. Reuses the wifi-channel-analyzer pattern. |
| `src/main.cpp` | Firmware: scan, debounce, CSV emit, `authModeString()`, `rssiToDistance()`. |
| `capture.py` | Live serial logger; buffers rows until the firmware's `# spot=...` footer. |
| `heatmap.py` | Per-SSID bar chart (no coords) or scatter heatmap (with `--coords`). |
| `README.md` | User-facing build/usage guide and CSV schema. |

## CSV Schema (firmware ↔ Python contract)

Header (printed at boot, validated by `capture.py`):

```
# spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,auth_mode,est_distance_m
```

Footer comment that marks a spot boundary:

```
# spot=<id> label=<label> ap_count=<n> scan_ms=<duration>
```

`capture.py` will abort if the header does not contain `est_distance_m` — keep firmware and Python in sync.

## Pitfalls

* Don't change `BUTTON_PIN` away from 0 without updating the README.
* Don't change the CSV header without updating `capture.py`'s `HEADER_RE`.
* RSSI→distance is capped at 10 m and is a rough path-loss estimate — not ground truth.
* The ESP32 can only scan one channel at a time; full-band scans take ~4 s at 300 ms/channel.
