# Radio Monitor Mode — Design

> **Date:** 2026-06-25
> **Status:** Approved (pending implementation)
> **Author:** Brainstorming session with the user

## Context

The project today has two operating modes:

1. **Spatial survey** (the documented happy path): walk around with the ESP32, press BOOT at each spot, capture one CSV file. `heatmap.py` then produces per-SSID bar charts or 2-D scatter heatmaps on a floor plan.
2. **Live radar** (added in commits `6fb4cff` and `33446c0`, issues #27 and #28): a Python-side SSE dashboard served on `http://127.0.0.1:8080` that visualizes devices as they probe, fed by `wifiscan/device_tracker.py`.

What neither mode answers well: **"what is my radio environment doing over time, and how is it changing?"** — the question a single, stationary receiver is uniquely positioned to answer. The user explicitly asked for this to be the next direction.

## Problem

With one stationary receiver, you cannot get real 2-D position (no trilateration, no angle of arrival). You also cannot keep the user in the browser-radar mental model: the angle there is `hash(mac) % 360`, which is decorative, not spatial.

What you **can** measure, and what is genuinely actionable, is the **temporal evolution** of the radio environment:

- RSSI stability per AP (router degradation, antenna issues, interference)
- Channel utilization over time (congestion patterns)
- Device presence patterns (which MACs are seen when, for how long)
- Drift between sessions (a baseline-vs-current comparison)

The current firmware forces every scan to be triggered by a BOOT button press and tied to a user-typed spot label. That model is wrong for the temporal use case.

## Solution

Add a new **monitor mode** alongside the existing spatial survey. Same hardware, same firmware, same CSV schema; different capture cadence and a new offline visualization tool.

### In scope (this iteration)

1. **Two new firmware commands** in `handleCommand()`:
   - `!scan` — trigger a scan immediately from the host (equivalent to BOOT).
   - `!monitor on [interval_ms]` / `!monitor off` — toggle automatic background scanning at a configurable interval. Default 5000 ms, minimum 1000 ms. While monitor mode is on, scans emit rows with `spot_label=monitor`.
2. **A pure helper** `monitorTick(last_scan_ms, now_ms, interval_ms) -> bool` declared `inline` in `src/wifi_scan_util.h` (no Arduino deps). Trivial subtraction-and-compare, kept header-only so the host test build can link it without pulling in the full Arduino framework.
3. **A new Python tool** `monitor.py` that:
   - Reads the same CSV format as `heatmap.py` (consumes `wifiscan/schema.py`).
   - Emits a single PNG with a grid of sparklines, one per AP.
   - Has a single result type `Timeseries` and a registry of plot adapters (`sparkline` implemented today; `presence`, `channel`, `drift` registered as `NotImplementedError` with a pointer to the deferred-features doc).
4. **Documentation**: `docs/monitor-deferred.md` for the four visualizations we are not building yet, with a brief sketch of each. README gets a "Monitor mode" section with the workflow and a cross-reference.

### Out of scope (deferred — see `docs/monitor-deferred.md`)

- Channel utilization chart
- Presence timeline (heatmap of MACs over time buckets)
- Drift / anomaly detection (rule-based, post-capture, PC-side only)
- Multi-receiver triangulation (≥3 ESP32 + clock sync + trilateralization)
- Live sparkline view in the existing `wifiscan/live.py` dashboard
- Alerting (push notifications, email, etc.)

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  ESP32 firmware (src/main.cpp + src/wifi_scan_util)          │
│                                                              │
│  handleCommand()                                             │
│    ├─ !scan             → logCurrentSpot()                   │
│    ├─ !monitor on [ms]  → g_monitor_on = true; label="monitor"│
│    └─ !monitor off      → g_monitor_on = false               │
│                                                              │
│  loop()                                                      │
│    ├─ BOOT pressed    → logCurrentSpot()                     │
│    └─ monitorTick(last, now, interval) && !busy → logCurrentSpot()  │
└────────────────────────┬─────────────────────────────────────┘
                         │ USB serial CSV
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  PC tooling (Python)                                         │
│                                                              │
│  capture.py (UNCHANGED)        heatmap.py (UNCHANGED)         │
│        │                              │                      │
│        └──────────┬───────────────────┘                      │
│                   ▼                                          │
│            logs/sesion.csv                                   │
│                   │                                          │
│                   ▼                                          │
│          wifiscan/timeseries.py (NEW)                        │
│          load_timeseries(csv) -> Timeseries                  │
│                   │                                          │
│                   ▼                                          │
│          monitor.py (NEW)                                    │
│          plot_sparkline(ts)   ← today                        │
│          plot_presence(ts)    ← NotImplementedError          │
│          plot_channel(ts)     ← NotImplementedError          │
│          plot_drift(ts, base) ← NotImplementedError          │
└──────────────────────────────────────────────────────────────┘
```

### Key design decisions

1. **The CSV remains the single seam.** `monitor.py` reads `wifiscan/schema.py`; it does not invent a new format. The schema version stays at 2; no new column.
2. **`capture.py` is not touched.** Monitor mode writes to the same `logs/signal_map_*.csv` files the user already produces. No parallel pipeline.
3. **`!scan` and `!monitor` collapse to one underlying function.** Both ultimately call `logCurrentSpot()`. The only difference is who decides when.
4. **`monitorTick` is a pure function.** It takes `(last_scan_ms, now_ms, interval_ms)`, all `uint32_t` in milliseconds, and returns `bool`. No state mutation, no side effects, no I/O. This is the only new piece of firmware logic worth testing in isolation, and the only one that can be tested without a board. The body is one unsigned subtraction plus a comparison.
5. **`monitor.py` exposes a `Timeseries` result type.** All four plot adapters consume the same `Timeseries`. Adding a new visualization is one new function, not a refactor.
6. **The "monitor" label is set by firmware, not by user input.** When monitor mode is enabled, the firmware overwrites `g_spot_label` with `"monitor"`. Documented as: do not type spot labels while monitor is on.

### Firmware changes (concrete)

- `src/wifi_scan_util.h` — declare `monitorTick(last_scan_ms, now_ms, interval_ms)` as an `inline` function. No `.cpp` change required; the header stays free of `<Arduino.h>` (it already includes only `<stddef.h>`, `<stdint.h>`, `<math.h>`).
- `src/main.cpp`:
  - Add globals `g_monitor_on`, `g_monitor_interval_ms`, `g_last_monitor_scan_ms`.
  - Extend `handleCommand()` with the `!scan` and `!monitor` branches. They follow the same shape as `!dwell` / `!channel`: tokenize, validate, set, echo a `# ` confirmation, `Serial.flush()`.
  - In `loop()`, before the button check, evaluate `monitorTick(...)` and call `logCurrentSpot()` on the rising edge. Guard against re-entry while a scan is in flight.
  - In `logCurrentSpot()` (or just before calling it from monitor path), overwrite `g_spot_label` to `"monitor"`. Alternative: refactor `logCurrentSpot()` to take a label parameter. The latter is cleaner; defer to the implementation plan.

### Python changes (concrete)

- `wifiscan/timeseries.py` (new) — `load_timeseries(path) -> Timeseries`. `Timeseries` is a `dict[str, list[tuple[int, int]]]` keyed by BSSID, value is list of `(timestamp_ms, rssi)`. Pure function; uses `pandas.read_csv` and `wifiscan/schema.EXPECTED_COLUMNS` for validation.
- `monitor.py` (new) — CLI. Mirrors `heatmap.py`'s argparse shape. Calls `load_timeseries`, then dispatches to the chosen `plot_*` function. Default kind: `sparkline`.
- `tests/test_timeseries.py` (new) — parse CSVs, return shape, edge cases (empty, single BSSID, single row, missing RSSI).
- `tests/test_monitor.py` (new) — sparkline plot produces a non-empty PNG; kind dispatch works; `NotImplementedError` for the three deferred kinds.
- `test/test_wifi_scan_util/test_monitor_tick.cpp` (new) — native test of the pure helper. Interval edge cases.

### Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `!monitor` interval is too small → port saturation, OOM | Medium | Medium | Hard floor 1000 ms; validate in `handleCommand` |
| `!monitor` + `!promisc on` conflict on the radio | High | Low | Document the conflict. The first to claim the radio wins; the other silently stops. No need to add exclusion logic. |
| Long-running `monitor.py` session produces a CSV too big for pandas | Low | Medium | Document. If it bites, switch to chunked CSV reading. Don't pre-optimize. |
| Sparkline grid is unreadable when 30+ APs are visible (the user's current case) | High | Low | Add `--top N` to limit to the N most-seen APs. Default 12. |
| A deferred visualization gets added without a corresponding issue | Low | Low | All four deferred `plot_*` adapters are explicit `NotImplementedError` with a docstring pointing to the deferred-features doc. |

### Test strategy

- **Firmware native** (`pio test -e native`): `monitorTick` — true at exact interval, false just before, false on second call without time advance.
- **Python pytest**: `load_timeseries` shape, `plot_sparkline` produces a non-empty file, dispatch raises for deferred kinds.
- **No integration test for the firmware ↔ Python loop** (requires a real board). The CI suite covers the seams in isolation; the user's manual workflow covers the end-to-end.

### Documentation

- New: `docs/monitor-deferred.md` — one section per deferred visualization: what it would show, sketch of the figure, what data it needs, why it's deferred.
- Update: `README.md` — add "Monitor mode" section with the workflow (`!monitor on`, walk away, `!monitor off`, run `monitor.py`).
- Update: `CHANGELOG` if one exists (none today; not creating one in this iteration).

### Rollout

- Additive change: nothing existing breaks.
- New firmware commands are opt-in (`!scan` and `!monitor`).
- New tool is opt-in (`python monitor.py ...`).
- CSV schema unchanged.
- If the user does nothing, behavior is identical to today.

## Related issues

- Existing: #1 (promiscuous mode coexistence), #22 (runtime commands), #27 (live dashboard).
- New (already open on GitHub): #30 (presence), #33 (channel), #34 (drift), #32 (multi-receiver triangulation), #31 (!monitor / !promisc radio conflict).
