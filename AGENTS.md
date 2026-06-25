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
* Don't change the CSV header without updating the centralized schema in `wifiscan/schema.py` — `capture.py` reads `HEADER_LINE` from there, not a local `HEADER_RE` regex.
* RSSI→distance is capped at 10 m and is a rough path-loss estimate — not ground truth.
* The ESP32 can only scan one channel at a time; full-band scans take ~4 s at 300 ms/channel.
* `spot_label` must be CSV-escaped in all four serial output sites — the firmware uses `csvEscape()` from `wifi_scan_util.h` (issue #4, `src/main.cpp`).
* `Serial.readStringUntil` was replaced with a bounded `readBytesUntil` read; pasting large inputs is silently dropped rather than OOM-crashing the board (issue #8, `src/main.cpp` `readSerialLabel()`).
* The CSV schema lives in `wifiscan/schema.py` (Python) and `firmware_header.txt` (checked in); both must be kept in sync via `tools/check_schema.py` (issues #6, #13).
* The schema module exports `SCHEMA_VERSION` (currently 2); bumping it requires coordinated changes in `wifiscan/schema.py`, `src/main.cpp`, `firmware_header.txt`, and `capture.py` (issue #16).
* The PlatformIO `native` test env covers pure firmware functions only (`wifi_scan_util.{h,cpp}`); non-pure code (`WiFi`, `Serial`, `debounced_button`, `io_abstractions`) is excluded from native tests (issue #9, `platformio.ini` `[env:native]`).
* CSV injection mitigation: `_safe_field()` in `wifiscan/schema.py` prefixes `'` to cells starting with `=`, `+`, `-`, `@` — applied only to free-text columns, not numeric ones (RSSI is always negative) (issue #18).
* Runtime commands (`!dwell`, `!channel`, `!ignore`, `!promisc`) are parsed in `handleCommand()` in `src/main.cpp` — the ignore list is in-memory and lost on reboot (issues #22, #23, #1).
* Promiscuous mode (`!promisc on`) coexists with active scan by temporarily disabling promisc during `WiFi.scanNetworks()` (issue #1, `src/main.cpp` `logCurrentSpot()`).

## How to make a change

1. **CSV schema changes**: Edit `EXPECTED_COLUMNS` in `wifiscan/schema.py`, then bump `SCHEMA_VERSION` (both there and in `src/main.cpp`). Run `python tools/check_schema.py` to verify sync. Regenerate `firmware_header.txt` with `python tools/sync_firmware_header.py`.
2. **Run tests before committing**: `pio test -e native` (firmware pure functions), `python -m pytest tests/ -q` (Python: capture, heatmap, merge, schema).
3. **CI**: GitHub Actions runs both test suites on every push/PR to `main` (see `.github/workflows/ci.yml`).

## Agent skills

### Issue tracker

GitHub issues (via `gh` CLI). External PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
