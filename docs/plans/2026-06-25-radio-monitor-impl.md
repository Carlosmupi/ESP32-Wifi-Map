# Radio Monitor Mode — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "monitor" operating mode to the Wi-Fi Scanner (firmware + Python) that supports a single stationary receiver answering "how is my radio environment changing over time?" via per-AP sparkline visualizations.

**Architecture:** New firmware commands `!scan` and `!monitor` reuse the existing `logCurrentSpot()` path. A new pure helper `monitorTick(state, now_ms)` in `wifi_scan_util` is unit-tested on the host. A new `wifiscan/timeseries.py` loads CSVs into a shared `Timeseries` type. A new `monitor.py` CLI dispatches to plot adapters; `plot_sparkline` is implemented today, the other three raise `NotImplementedError`.

**Tech Stack:** C++ (Arduino-ESP32, native Unity tests), Python 3.11 (pandas, matplotlib), PlatformIO, pytest, GitHub Actions (CI).

**Design doc:** `docs/plans/2026-06-25-radio-monitor-design.md`

**Working directory:** All commands assume the repo root unless noted.

---

## Phase 0: Sanity check (1 task)

### Task 0.1: Confirm clean baseline

**Step 1:** Run the existing test suites. They MUST pass before any change.

```bash
cd "C:/Dev/Embedded/projects/wifi-scanner-signal-map"
pio test -e native
python -m pytest tests/ -q
```

**Expected:** Both exit 0. If anything fails, stop and resolve.

**Step 2:** Verify the existing tools still work on the captured log.

```bash
MPLBACKEND=Agg python heatmap.py logs/signal_map_20260625_142358.csv --combined --combined-max 9
```

**Expected:** Writes one PNG, prints a per-AP summary table.

**Step 3:** Commit nothing (sanity check only).

---

## Phase 1: Firmware — pure helper (3 tasks)

### Task 1.1: Declare `monitorTick` in the public header

**Files:**
- Modify: `src/wifi_scan_util.h` (add a new section after the existing `copyLabel` declaration, before the `class Clock` block)

**Step 1:** Open the file and find the end of the `copyLabel` declaration. Insert after it:

```cpp
// Monotonic-time scheduler for the `!monitor` background-scan loop.
// Pure function: takes the previous scan timestamp and the configured
// interval (both uint32_t in milliseconds) and returns true iff the
// caller should trigger another scan now. Rolling-over `now_ms` is
// handled by the unsigned subtraction (subtraction wraps mod 2^32).
//
// Inlined because the body is one comparison and the call site is
// the ESP32's `loop()`. Keeping it header-only avoids a translation
// unit dependency for native tests.
inline bool monitorTick(uint32_t last_scan_ms, uint32_t now_ms,
                        uint32_t interval_ms) {
    return (now_ms - last_scan_ms) >= interval_ms;
}
```

**Step 2:** Compile only — do not run native tests yet.

```bash
pio test -e native --without-uploading
```

**Expected:** Build succeeds. The new symbol is unused-warning-clean because the test in Task 1.2 will reference it.

**Step 3:** No commit yet — the test will land this together with the implementation.

---

### Task 1.2: Add the native test for `monitorTick` (TDD: red)

**Files:**
- Create: `test/test_wifi_scan_util/test_monitor_tick.cpp`

**Step 1:** Write the test file. Use the same Unity pattern as `test_rssi_to_distance.cpp`.

```cpp
// test_monitor_tick.cpp — Unity tests for the inline `monitorTick` helper
// in src/wifi_scan_util.h. Host-only; does not require the ESP32 toolchain.
//
// Follows the suite's existing convention: test functions have external
// linkage and are wired into Unity by test_main.cpp (the single TU that
// defines main()). setUp()/tearDown() are intentionally NOT defined here
// — unity_config.c provides weak empty versions, per the comment at
// test_main.cpp:10-13.

#include <unity.h>

#include "wifi_scan_util.h"  // already brings in <stdint.h> for uint32_t

void test_first_call_after_interval_fires(void) {
    // Interval of 1000 ms; last scan at t=0; now at t=1000.
    TEST_ASSERT_TRUE(monitorTick(0u, 1000u, 1000u));
}

void test_just_before_interval_does_not_fire(void) {
    TEST_ASSERT_FALSE(monitorTick(0u, 999u, 1000u));
}

void test_repeat_call_without_time_advance_does_not_fire(void) {
    // First fire consumes nothing — `monitorTick` does not mutate state.
    TEST_ASSERT_TRUE(monitorTick(0u, 1000u, 1000u));
    TEST_ASSERT_FALSE(monitorTick(0u, 1000u, 1000u));
}

void test_unsigned_rollover_fires(void) {
    // 2^32 - 1 ms after the last scan should still fire.
    const uint32_t last = 1u;
    const uint32_t now  = 0u;  // wrapped
    TEST_ASSERT_TRUE(monitorTick(last, now, 1000u));
}

void test_zero_interval_fires_every_call(void) {
    TEST_ASSERT_TRUE(monitorTick(0u, 0u, 0u));
    TEST_ASSERT_TRUE(monitorTick(0u, 1u, 0u));
}
```

**Step 2:** Register the five new tests in `test/test_wifi_scan_util/test_main.cpp` (the suite has exactly one `main()`; per-function files declare their `test_xxx()` routines there).

Open `test/test_wifi_scan_util/test_main.cpp` and add **two** things:

1. In the `extern` declaration block, after the `authModeString` externs and before the `copyLabel` externs, add:

```cpp
// ---- monitorTick (test_monitor_tick.cpp) -------------------------
extern void test_first_call_after_interval_fires(void);
extern void test_just_before_interval_does_not_fire(void);
extern void test_repeat_call_without_time_advance_does_not_fire(void);
extern void test_unsigned_rollover_fires(void);
extern void test_zero_interval_fires_every_call(void);
```

2. In the `RUN_TEST(...)` block inside `main()`, after the authModeString `RUN_TEST`s and before the copyLabel `RUN_TEST`s, add:

```cpp
    RUN_TEST(test_first_call_after_interval_fires);
    RUN_TEST(test_just_before_interval_does_not_fire);
    RUN_TEST(test_repeat_call_without_time_advance_does_not_fire);
    RUN_TEST(test_unsigned_rollover_fires);
    RUN_TEST(test_zero_interval_fires_every_call);
```

Do not define `setUp()`, `tearDown()`, `setup()`, or `loop()` in this test file — see test_main.cpp:10-13.

**Step 3:** Build and run.
```bash
pio test -e native
```

**Expected:** All five new tests PASS. The header-only implementation in Task 1.1 makes the red→green step actually a no-op in terms of new code; the test file is the deliverable. If any test fails, the implementation in `wifi_scan_util.h` is wrong — fix the header.

**Step 4:** Commit.

```bash
git add src/wifi_scan_util.h test/test_wifi_scan_util/test_monitor_tick.cpp test/test_wifi_scan_util/test_main.cpp
git commit -m "test(firmware): add monitorTick unit tests (monitor mode)"
```

---

### Task 1.3: Verify native test coverage is picked up by CI

**Files:**
- Modify: `.github/workflows/ci.yml` (only if needed; check first)

**Step 1:** Open `.github/workflows/ci.yml`. Confirm there is a step that runs `pio test -e native`. If yes, no change needed. If no, add it under the firmware job.

**Step 2:** No code change; just a verification. Skip if CI is already configured.

---

## Phase 2: Firmware — runtime commands (3 tasks)

### Task 2.1: Add `!scan` to `handleCommand()`

**Files:**
- Modify: `src/main.cpp` (the `handleCommand` function, before the unknown-cmd fallback at the end)

**Step 1:** Read the current `handleCommand()` to confirm the structure (already done in exploration: it tokenizes with `strtok`, branches on `strcmp(cmd, "!…")`, prints a `# …` confirmation, calls `Serial.flush()`, returns `true`).

**Step 2:** Add the `!scan` branch before the `// Unknown command` comment. Place it after the `!ignorelist` block, before `!promisc`:

```cpp
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
```

**Step 3:** Add the `g_scan_in_flight` global near the other scan state. Near `g_promisc_on`:

```cpp
bool        g_scan_in_flight = false;
```

**Step 4:** Set `g_scan_in_flight = true` at the top of `logCurrentSpot()` and `false` at every exit path (the early return on `n <= 0` and the fall-through after the footer). Read the current `logCurrentSpot()` to find the exact return points; there are two.

**Step 5:** Compile (no test on host — the host stub does not provide `logCurrentSpot`):

```bash
pio run
```

**Expected:** Compiles clean. (Do not upload — the user has not asked for a board flash yet.)

**Step 6:** Commit.

```bash
git add src/main.cpp
git commit -m "feat(firmware): add !scan runtime command (monitor mode)"
```

---

### Task 2.2: Add `!monitor` to `handleCommand()`

**Files:**
- Modify: `src/main.cpp`

**Step 1:** Add globals near the others:

```cpp
bool        g_monitor_on            = false;
uint32_t    g_monitor_interval_ms   = 5000;
uint32_t    g_last_monitor_scan_ms  = 0;
constexpr uint32_t MONITOR_MIN_INTERVAL_MS = 1000;
constexpr uint32_t MONITOR_DEFAULT_INTERVAL_MS = 5000;
```

**Step 2:** Add the `!monitor` branch in `handleCommand()`, after `!scan`:

```cpp
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
```

**Step 3:** Compile.

```bash
pio run
```

**Expected:** Compiles clean.

**Step 4:** Commit.

```bash
git add src/main.cpp
git commit -m "feat(firmware): add !monitor runtime command (monitor mode)"
```

---

### Task 2.3: Wire the monitor tick into `loop()`

**Files:**
- Modify: `src/main.cpp` (the `loop()` function)

**Step 1:** Read `loop()` (lines 600-617 of the current file; elided in the initial read — re-read with `:600-`). Find the button-press check.

**Step 2:** Add the monitor tick check just before the button check, so the monitor path is evaluated on every loop regardless of button state:

```cpp
    if (g_monitor_on && !g_scan_in_flight &&
        monitorTick(g_last_monitor_scan_ms, millis(), g_monitor_interval_ms)) {
        logCurrentSpot();
        // Update AFTER the scan returns. A full-band scan takes ~4 s; with
        // a 1 s interval, setting this before the call would re-fire the
        // moment the scan finishes (saturation). The interval is now the
        // gap between scan ends, not between scan starts.
        g_last_monitor_scan_ms = millis();
    }
```

**Step 3:** Compile.

```bash
pio run
```

**Expected:** Compiles clean.

**Step 4:** Commit.

```bash
git add src/main.cpp
git commit -m "feat(firmware): drive !monitor scans from loop() (monitor mode)"
```

---

## Phase 3: Python — `wifiscan/timeseries.py` (3 tasks)

### Task 3.1: Add the failing test for `load_timeseries` (TDD: red)

**Files:**
- Create: `tests/test_timeseries.py`

**Step 1:** Write the test file. Mirror the `test_heatmap.py` boilerplate exactly (sys.path insert, `pandas` / `wifiscan` skip, helper functions).

```python
"""Pytest suite for wifiscan.timeseries.

Covers the CSV→Timeseries load: row parsing, per-BSSID grouping, RSSI
extraction, and edge cases (empty input, single BSSID, single row).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pd = pytest.importorskip("pandas")
pytest.importorskip("wifiscan")

from wifiscan.schema import EXPECTED_COLUMNS  # noqa: E402


def _row(bssid: str, rssi: int, ts: int, ssid: str = "Net") -> dict:
    return {
        "spot_id": 0,
        "spot_label": "monitor",
        "timestamp_ms": ts,
        "ssid": ssid,
        "bssid": bssid,
        "rssi": rssi,
        "channel": 6,
        "auth_mode": "WPA2_PSK",
        "est_distance_m": 1.0,
        "frame_type": "ap",
        "src_mac": "",
    }


def _write_csv(tmp_path: Path, rows: list[dict]) -> Path:
    df = pd.DataFrame(rows, columns=list(EXPECTED_COLUMNS))
    p = tmp_path / "session.csv"
    # Match the on-disk shape capture.py produces (see capture.py
    # write_header: csv.DictWriter with fieldnames=EXPECTED_COLUMNS, no
    # '#' prefix, no index column). load_timeseries reads with the
    # default pd.read_csv, so the fixture must look like that.
    df.to_csv(p, index=False)
    return p


def test_load_returns_dict_keyed_by_bssid(tmp_path):
    from wifiscan.timeseries import load_timeseries
    rows = [
        _row("aa:bb:cc:dd:ee:01", -50, 1000),
        _row("aa:bb:cc:dd:ee:02", -60, 1100),
        _row("aa:bb:cc:dd:ee:01", -55, 2000),
    ]
    ts = load_timeseries(_write_csv(tmp_path, rows))
    assert set(ts.keys()) == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}
    assert ts["aa:bb:cc:dd:ee:01"] == [(1000, -50), (2000, -55)]
    assert ts["aa:bb:cc:dd:ee:02"] == [(1100, -60)]


def test_load_sorts_by_timestamp_per_bssid(tmp_path):
    from wifiscan.timeseries import load_timeseries
    rows = [
        _row("aa:bb:cc:dd:ee:01", -50, 3000),
        _row("aa:bb:cc:dd:ee:01", -55, 1000),
        _row("aa:bb:cc:dd:ee:01", -60, 2000),
    ]
    ts = load_timeseries(_write_csv(tmp_path, rows))
    assert ts["aa:bb:cc:dd:ee:01"] == [(1000, -55), (2000, -60), (3000, -50)]


def test_load_empty_csv_returns_empty_dict(tmp_path):
    from wifiscan.timeseries import load_timeseries
    # An empty rows list produces a header-only CSV — same shape as
    # capture.py's write_header() before any spot is appended.
    p = _write_csv(tmp_path, [])
    ts = load_timeseries(p)
    assert ts == {}


def test_load_skips_rows_missing_rssi(tmp_path):
    from wifiscan.timeseries import load_timeseries
    df = pd.DataFrame(
        [
            _row("aa:bb:cc:dd:ee:01", -50, 1000),
            _row("aa:bb:cc:dd:ee:01", None, 2000),  # NaN RSSI
        ],
        columns=list(EXPECTED_COLUMNS),
    )
    p = tmp_path / "with_nan.csv"
    df.to_csv(p, index=False)  # normal header, matches capture.py shape
    ts = load_timeseries(p)
    assert ts["aa:bb:cc:dd:ee:01"] == [(1000, -50)]


def test_load_rejects_csv_missing_required_columns(tmp_path):
    from wifiscan.timeseries import load_timeseries
    # Header is missing the required columns (bssid, timestamp_ms, rssi);
    # load_timeseries must raise SystemExit with the missing names.
    # No '#' prefix — the on-disk format is a normal CSV header.
    p = tmp_path / "bad.csv"
    p.write_text("spot_id,spot_label\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_timeseries(p)

**Step 2:** Run the tests. All must FAIL (`ModuleNotFoundError` on `wifiscan.timeseries`).

```bash
python -m pytest tests/test_timeseries.py -q
```

**Expected:** `5 failed, ModuleNotFoundError`.

**Step 3:** No commit yet — implementation lands with Task 3.2.

---

### Task 3.2: Implement `load_timeseries` (TDD: green)

**Files:**
- Create: `wifiscan/timeseries.py`

**Step 1:** Write the module.

```python
"""wifiscan.timeseries — load Wi-Fi Scanner CSVs into per-BSSID time series.

The single public function ``load_timeseries(path)`` returns a mapping
``{bssid: [(timestamp_ms, rssi), ...]}`` with one entry per BSSID and
the observations sorted by timestamp. Rows with a missing RSSI are
silently dropped (a real ESP32 will not produce them, but a hand-
edited CSV might).

Used by ``monitor.py`` to build the per-AP sparkline grid and (later)
the presence, channel, and drift visualizations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from wifiscan.schema import EXPECTED_COLUMNS

__all__ = ["load_timeseries", "Timeseries"]

#: Type alias for the load result: BSSID -> ordered (timestamp_ms, rssi) pairs.
Timeseries = Dict[str, List[Tuple[int, int]]]

_REQUIRED = ("bssid", "timestamp_ms", "rssi")


def load_timeseries(path: Path) -> Timeseries:
    """Read a Wi-Fi Scanner CSV and group its rows by BSSID.

    The CSV is the on-disk format produced by ``capture.py``: a normal
    ``csv.DictWriter`` header (no ``#`` prefix) followed by data rows.
    The parser uses :data:`wifiscan.schema.EXPECTED_COLUMNS` for column
    validation, then drops any row whose RSSI is not a number. This is
    the same ``pd.read_csv(path)`` call shape as :mod:`heatmap`; keep
    the two in sync.
    """
    df = pd.read_csv(path)
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(
            f"CSV missing required columns for timeseries: {sorted(missing)}"
        )
    # Firmware may emit extra columns (frame_type, src_mac, etc.); keep them
    # in the DataFrame but extract only what we need.
    sub = df[["bssid", "timestamp_ms", "rssi"]].dropna(subset=["rssi"])
    sub["timestamp_ms"] = sub["timestamp_ms"].astype("int64")
    sub["rssi"] = sub["rssi"].astype("int64")
    out: Timeseries = {}
    for bssid, group in sub.groupby("bssid", sort=False):
        out[bssid] = sorted(
            zip(group["timestamp_ms"].tolist(), group["rssi"].tolist())
        )
    return out
```

**Step 2:** Re-run the tests. All five must PASS.

```bash
python -m pytest tests/test_timeseries.py -q
```

**Expected:** `5 passed`.

**Step 3:** Also re-run the full pytest suite to confirm no regression.

```bash
python -m pytest tests/ -q
```

**Expected:** All green (the new file is a pure add).

**Step 4:** Commit.

```bash
git add wifiscan/timeseries.py tests/test_timeseries.py
git commit -m "feat(python): add wifiscan.timeseries.load_timeseries (monitor mode)"
```

---

## Phase 4: Python — `monitor.py` CLI (4 tasks)

### Task 4.1: Add the failing test for `plot_sparkline` (TDD: red)

**Files:**
- Create: `tests/test_monitor.py`

**Step 1:** Write the test file. Mirror `test_heatmap.py`'s top-of-file boilerplate.

```python
"""Pytest suite for monitor.py.

Covers:
  * plot_sparkline produces a non-empty PNG with one row per BSSID
  * the four --kind values resolve to the right plot function
  * deferred kinds raise NotImplementedError pointing to the docs
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pd = pytest.importorskip("pandas")
matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
pytest.importorskip("wifiscan")

import monitor  # noqa: E402
from wifiscan.timeseries import load_timeseries  # noqa: E402


def _row(bssid: str, rssi: int, ts: int) -> dict:
    return {
        "spot_id": 0,
        "spot_label": "monitor",
        "timestamp_ms": ts,
        "ssid": "Net",
        "bssid": bssid,
        "rssi": rssi,
        "channel": 6,
        "auth_mode": "WPA2_PSK",
        "est_distance_m": 1.0,
        "frame_type": "ap",
        "src_mac": "",
    }


def _csv(tmp_path: Path, rows: list[dict]) -> Path:
    from wifiscan.schema import EXPECTED_COLUMNS
    df = pd.DataFrame(rows, columns=list(EXPECTED_COLUMNS))
    p = tmp_path / "session.csv"
    # Match capture.py's on-disk shape: normal header, no '#' prefix,
    # no index column. See capture.py write_header() and the matching
    # format used in tests/test_timeseries.py::_write_csv.
    df.to_csv(p, index=False)
    return p


def test_sparkline_writes_png(tmp_path):
    rows = [_row("aa:bb:cc:dd:ee:01", -50 + i, 1000 * (i + 1))
            for i in range(20)]
    rows += [_row("aa:bb:cc:dd:ee:02", -70 - i, 1000 * (i + 1))
             for i in range(20)]
    p = _csv(tmp_path, rows)
    ts = load_timeseries(p)
    out = tmp_path / "sparkline.png"
    monitor.plot_sparkline(ts, out, top=10)
    assert out.exists()
    assert out.stat().st_size > 0


def test_sparkline_respects_top_limit(tmp_path):
    # 30 BSSIDs, but ask for top=5. PNG should still write and not error.
    rows = []
    for i in range(30):
        rows += [_row(f"aa:bb:cc:dd:ee:{i:02x}", -50, 1000 * k)
                 for k in range(1, 4)]
    p = _csv(tmp_path, rows)
    ts = load_timeseries(p)
    out = tmp_path / "sparkline_top.png"
    monitor.plot_sparkline(ts, out, top=5)
    assert out.exists()


def test_deferred_kinds_raise(tmp_path):
    p = _csv(tmp_path, [_row("aa:bb:cc:dd:ee:01", -50, 1000)])
    ts = load_timeseries(p)
    for kind in ("presence", "channel", "drift"):
        with pytest.raises(NotImplementedError):
            monitor.dispatch(kind, ts, tmp_path / f"{kind}.png", top=12)


def test_sparkline_dispatch(tmp_path):
    p = _csv(tmp_path, [_row("aa:bb:cc:dd:ee:01", -50, 1000),
                        _row("aa:bb:cc:dd:ee:01", -55, 2000)])
    ts = load_timeseries(p)
    out = tmp_path / "via_dispatch.png"
    monitor.dispatch("sparkline", ts, out, top=5)
    assert out.exists()
```

**Step 2:** Run the tests. All must FAIL (no `monitor` module yet).

```bash
python -m pytest tests/test_monitor.py -q
```

**Expected:** Collection error: `ModuleNotFoundError: No module named 'monitor'`.

---

### Task 4.2: Implement `monitor.py` with `plot_sparkline` (TDD: green)

**Files:**
- Create: `monitor.py` (at repo root, alongside `capture.py` and `heatmap.py`)

**Step 1:** Write the script.

```python
#!/usr/bin/env python3
"""
monitor.py — render a per-AP sparkline grid from a Wi-Fi Scanner CSV.

The CSV is the same one produced by `capture.py` while the firmware is
in `!monitor` mode. Each row in the CSV becomes one (timestamp, RSSI)
sample; the script groups samples by BSSID and draws a tiny line plot
per AP in a single grid figure.

Usage:
    python monitor.py path/to/session.csv
    python monitor.py path/to/session.csv --top 20
    python monitor.py path/to/session.csv --output out_dir/

Other `--kind` values (presence, channel, drift) are registered but
not yet implemented; see docs/monitor-deferred.md.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Callable, Dict, List, Tuple

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as exc:
    raise SystemExit(
        "matplotlib is required. Install with: pip install matplotlib"
    ) from exc

# Repo root on sys.path so `import wifiscan` works without an install.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wifiscan.timeseries import load_timeseries, Timeseries  # noqa: E402

__all__ = ["plot_sparkline", "dispatch", "PLOTTERS"]

#: Default visual range for the y-axis. Matches heatmap.py.
RSSI_VMIN = -95
RSSI_VMAX = -30

#: A safe filename for a BSSID. Falls back to the raw MAC if the
#: input cannot be sanitized (e.g. empty string after stripping).
def _safe_bssid(bssid: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in bssid)
    return cleaned or "unknown"


def _ranked_bssids(ts: Timeseries) -> List[Tuple[str, List[Tuple[int, int]]]]:
    """Return BSSIDs sorted by descending sample count, then BSSID.

    Each element of the result is ``(bssid, samples)`` where ``samples``
    is the full ordered list of ``(timestamp_ms, rssi)`` pairs for that
    BSSID (preserved from :data:`Timeseries`). The call site unpacks
    each sample as ``(t, r)`` — the annotation must therefore be
    ``List[Tuple[int, int]]`` per BSSID, not ``int``.
    """
    return sorted(ts.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _grid_dims(n: int) -> Tuple[int, int]:
    """Return (rows, cols) for a near-square grid of n panels."""
    if n <= 0:
        return 0, 0
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = math.ceil(n / cols)
    return rows, cols


def plot_sparkline(ts: Timeseries, out: Path, top: int = 12) -> None:
    """Write a per-BSSID sparkline grid to ``out``.

    Parameters
    ----------
    ts
        Output of :func:`wifiscan.timeseries.load_timeseries`.
    out
        Destination PNG path. Parent directory is created if missing.
    top
        Maximum number of BSSIDs to plot. The most-sampled BSSIDs win.
    """
    if not ts:
        raise SystemExit("monitor: empty timeseries; nothing to plot.")

    ranked = _ranked_bssids(ts)[: max(1, top)]
    rows, cols = _grid_dims(len(ranked))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.2, rows * 1.6),
                             squeeze=False)

    for ax, (bssid, samples) in zip(axes.flat, ranked):
        ts_ms = [t for t, _ in samples]
        rssi  = [r for _, r in samples]
        ax.plot(ts_ms, rssi, linewidth=0.9)
        ax.set_ylim(RSSI_VMIN, RSSI_VMAX)
        ax.set_title(_safe_bssid(bssid), fontsize=7, loc="left")
        ax.tick_params(axis="both", which="both", labelsize=6, length=0)
        ax.grid(True, linewidth=0.3, alpha=0.4)

    # Hide any unused axes (when the grid has more cells than BSSIDs).
    for ax in axes.flat[len(ranked):]:
        ax.set_visible(False)

    fig.suptitle("RSSI over time (per BSSID)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Deferred plot adapters
# ---------------------------------------------------------------------------

def _not_implemented(kind: str) -> None:
    raise NotImplementedError(
        f"monitor --kind {kind!r} is registered but not implemented yet. "
        "See docs/monitor-deferred.md for the design."
    )


def plot_presence(_ts: Timeseries, out: Path, top: int = 12) -> None:  # noqa: ARG001
    _not_implemented("presence")


def plot_channel(_ts: Timeseries, out: Path, top: int = 12) -> None:  # noqa: ARG001
    _not_implemented("channel")


def plot_drift(_ts: Timeseries, out: Path, top: int = 12) -> None:  # noqa: ARG001
    _not_implemented("drift")


#: Registry of --kind values to plot functions.
PLOTTERS: Dict[str, Callable[[Timeseries, Path, int], None]] = {
    "sparkline": plot_sparkline,
    "presence":  plot_presence,
    "channel":   plot_channel,
    "drift":     plot_drift,
}


def dispatch(kind: str, ts: Timeseries, out: Path, top: int) -> None:
    """Resolve ``--kind`` to a plot function and call it."""
    fn = PLOTTERS.get(kind)
    if fn is None:
        raise SystemExit(
            f"monitor: unknown --kind {kind!r}. "
            f"Known: {sorted(PLOTTERS)}"
        )
    fn(ts, out, top)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", type=Path, help="Path to a Wi-Fi Scanner CSV.")
    p.add_argument("--kind", choices=sorted(PLOTTERS), default="sparkline",
                   help="Visualization kind. Default: sparkline.")
    p.add_argument("--top", type=int, default=12,
                   help="Maximum BSSIDs to include. Default: 12.")
    p.add_argument("--output", type=Path, default=None,
                   help="Output directory. Default: alongside the CSV.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    ts = load_timeseries(args.csv)
    out_dir = args.output or args.csv.parent
    out_file = out_dir / f"{args.csv.stem}_{args.kind}.png"
    dispatch(args.kind, ts, out_file, args.top)
    print(f"[monitor] {len(ts)} BSSIDs -> {out_file.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 2:** Run the tests.

```bash
python -m pytest tests/test_monitor.py -q
```

**Expected:** All four tests PASS.

**Step 3:** Run the full pytest suite.

```bash
python -m pytest tests/ -q
```

**Expected:** All green.

**Step 4:** Commit.

```bash
git add monitor.py tests/test_monitor.py
git commit -m "feat(python): add monitor.py with sparkline plot (monitor mode)"
```

---

### Task 4.3: End-to-end smoke against the captured log

**Files:** none

**Step 1:** Run the new tool on the existing captured log. The CSV has 30 APs from one spot, so the sparkline will be a single bar per AP — this only verifies the pipeline doesn't error.

```bash
cd "C:/Dev/Embedded/projects/wifi-scanner-signal-map"
MPLBACKEND=Agg python monitor.py logs/signal_map_20260625_142358.csv --top 12
```

**Expected:** Prints `[monitor] 30 BSSIDs -> signal_map_20260625_142358_sparkline.png` and writes the file.

**Step 2:** Verify the file exists and is non-empty.

```bash
ls -la logs/signal_map_20260625_142358_sparkline.png
```

**Expected:** A file of >10 KB.

**Step 3:** Try the deferred kinds. Both should error out with the documented message.

```bash
MPLBACKEND=Agg python monitor.py logs/signal_map_20260625_142358.csv --kind presence
MPLBACKEND=Agg python monitor.py logs/signal_map_20260625_142358.csv --kind channel
```

**Expected:** Each exits non-zero with `NotImplementedError: monitor --kind '…' is registered but not implemented yet. See docs/monitor-deferred.md for the design.`

**Step 4:** No commit (smoke check only; the implementation was committed in 4.2).

---

## Phase 5: Documentation (5 tasks)

### Task 5.1: Write `docs/monitor-deferred.md`

**Files:**
- Create: `docs/monitor-deferred.md`

**Step 1:** Write the doc.

```markdown
# Deferred Monitor Visualizations

This document describes the three monitor visualizations registered in
`monitor.py --kind` but not yet implemented. Each section sketches the
intended figure, the data it needs (already available in the CSV
schema), and a usage example.

When a future iteration implements one of these, replace the
`NotImplementedError` stub in `monitor.py` and link the corresponding
issue here.

## Presence timeline (`--kind presence`)

A heatmap of MACs over time. Rows = BSSIDs (or src_mac, for
promiscuous-mode rows), columns = time buckets (default: 1 hour),
cell = number of observations in that bucket.

**What it answers:** which devices are around most often, and at what
times. Useful for spotting visitors, neighbor patterns, or device
schedules.

**Sketch:**

```
BSSID / src_mac      00   01   02   03   04   05   06   07   08 ...
aa:bb:cc:dd:ee:01    ░    ░    ▓    █    █    █    ▓    ░    ░
aa:bb:cc:dd:ee:02    ░    ░    ░    ░    ▓    ▓    ░    ░    ░
probe:aa:bb:cc:01    ░    ▓    ▓    ░    ░    ░    █    █    ▓
```

**Status:** Not implemented. Tracked in issue #30.

## Channel utilization (`--kind channel`)

A stacked bar chart showing how many APs are visible on each
2.4 GHz channel over time. Identifies the least-congested channel
for the user's own router.

**What it answers:** which channel is free right now? Which one
gets crowded at peak hours?

**Sketch:**

```
APs visible
10 │        ▓▓▓
 8 │   ░▓▓░ ▓▓▓ ░▓▓
 6 │   ▓▓▓ ▓▓▓ ░▓▓ ░▓▓
 4 │   ░░░ ░░░ ░░░ ░░░
 2 │   ░░░ ░░░ ░░░ ░░░
 0 └────────────────────
     1   6  11  13
        channel
```

**Status:** Not implemented. Tracked in issue #33.

## Drift / anomaly detection (`--kind drift`)

A two-line chart comparing a "current" session to a "baseline"
session (typically last week, last month, or the previous run).
The user supplies the baseline as a second CSV on the command line.
The plot shows RSSI-over-time for the union of BSSIDs, with the
median delta annotated per BSSID.

**What it answers:** did something change? Which APs got weaker,
which got stronger, which appeared or disappeared?

**Sketch:**

```
RSSI
-30 ┤
-40 ┤        ─── current (MiFibra-22A7)
-50 ┤   ─ ─  baseline
-60 ┤
-70 ┤
    └────────────────────
      00:00     12:00     24:00
```

**Status:** Not implemented. Tracked in issue #34.

## Out of scope entirely

### Multi-receiver triangulation

With one receiver, real 2-D positioning is impossible. A future
iteration could deploy ≥3 ESP32 boards with clock sync, each running
`!monitor`, and a host-side service that fuses their per-BSSID RSSI
streams into trilateralized device positions. This is a substantial
new project, not a feature of this tool.

Tracked in issue #32.
```

**Step 2:** Commit.

```bash
git add docs/monitor-deferred.md
git commit -m "docs: deferred monitor visualizations"
```

---

### Task 5.2: Update README with the Monitor mode section

**Files:**
- Modify: `README.md`

**Step 1:** Read the current "Runtime Commands" section to find the insertion point.

**Step 2:** Add a new "Monitor mode" section after the existing "Runtime Commands" section. Place it before "## Python Tools":

```markdown
## Monitor Mode

Monitor mode is the time-series counterpart to the spatial survey.
Instead of pressing BOOT at each spot, leave the ESP32 in one place
and let it scan on a schedule. The resulting CSV is the same format
as a survey session; a separate tool, `monitor.py`, renders a
per-BSSID sparkline grid.

### Workflow

1. Open the serial monitor.
2. Type `!monitor on 5000` (interval is optional, default 5000 ms,
   minimum 1000 ms) and press Enter. The firmware will set the
   spot label to `monitor` and start scanning with at least 5 s
   between scans (the interval is the gap between scan *ends*;
   a full-band scan takes ~4 s, so the effective period at 5 s
   is ~9 s — see Task 2.3 in the implementation plan).
3. Walk away. The LED still blinks on every scan.
4. When done, type `!monitor off`.
5. Run `python monitor.py logs/signal_map_<timestamp>.csv` to
   produce a sparkline grid.

The CSV is captured automatically by `capture.py` running in parallel
or can be captured by any other serial logger. A new session's
filename is `signal_map_<YYYYMMDD_HHMMSS>.csv` under `logs/`.

### Sparkline output

```bash
# Default: top 12 BSSIDs, write alongside the CSV.
python monitor.py logs/signal_map_20260625_142358.csv

# Include more BSSIDs.
python monitor.py logs/signal_map_20260625_142358.csv --top 30
```

The output PNG is `<csv-stem>_sparkline.png` and contains a near-
square grid of small line charts, one per BSSID, in the order of
most-sampled first.

### Triggering a scan on demand

`!scan` (no arguments) triggers one scan immediately. Useful for
scripted capture from the host or for forcing a fresh reading
mid-session.

### Deferred visualizations

Three more `--kind` values are registered in `monitor.py` but
raise `NotImplementedError`: `presence`, `channel`, `drift`. See
`docs/monitor-deferred.md` for the design sketches and the issues
that track them.

### Session length and `spot_id` rollover

The firmware's `spot_id` is a `uint16` (0 to 65535) and is incremented
once per monitor-mode scan. At the default 5 s interval, it rolls over
after about 91 hours of continuous capture; at the 1 s minimum, about
18 hours. The CSV is unchanged across a rollover (the firmware just
restarts counting from 0) but any analysis tool that treats `spot_id`
as a global order will see a discontinuity. For sessions expected to
run longer, capture into separate files.

**Step 3:** Commit.

```bash
git add README.md
git commit -m "docs(readme): add Monitor mode section"
```

---

### Task 5.3: Update AGENTS.md pitfall entry

**Files:**
- Modify: `AGENTS.md`

**Step 1:** Read the existing "Pitfalls" list at the bottom of the file. Find a place to add a new bullet.

**Step 2:** Add a new bullet:

```markdown
* `!monitor` and `!promisc on` cannot run simultaneously: the radio
  cannot be in promiscuous mode while performing an active scan.
  The first to claim the radio wins. To switch, turn the other off
  first. Tracked in issue #31.
```

**Step 3:** Commit.

```bash
git add AGENTS.md
git commit -m "docs(agents): document !monitor / !promisc radio conflict"
```

---

### Task 5.4: Add `monitor` to the pytest coverage scope

**Files:**
- Modify: `.github/workflows/ci.yml`

**Step 1:** Find the line:

```yaml
      - name: Run pytest with coverage
        run: pytest --cov=wifiscan --cov=capture --cov=heatmap tests/
```

Change it to:

```yaml
      - name: Run pytest with coverage
        run: pytest --cov=wifiscan --cov=capture --cov=heatmap --cov=monitor tests/
```

This keeps the new tool on equal footing with the existing root-level
scripts; without it, coverage for `monitor.py` is silently absent
from the CI report.

**Step 2:** Commit.

```bash
git add .github/workflows/ci.yml
git commit -m "ci: include monitor in pytest coverage scope"
```

---

### Task 5.5: Add `monitor.py` to the mypy scope

**Files:**
- Modify: `pyproject.toml`

**Step 1:** The mypy configuration at `pyproject.toml:35` only lists
the `wifiscan` package, `capture.py`, and `heatmap.py`. Without
`monitor.py` in the list, `python -m mypy` passes by silently
ignoring the new module — false confidence. Fix the scope so the
CI gate actually verifies the new code.

Find:

```toml
files = ["wifiscan", "capture.py", "heatmap.py"]
```

Change to:

```toml
files = ["wifiscan", "capture.py", "heatmap.py", "monitor.py"]
```

Also update the comment block above the `[tool.mypy]` section so
the next person reading it knows `monitor.py` is intentionally
included (the comment currently enumerates the scripts and should
list `monitor.py` too).

**Step 2:** Commit.

```bash
git add pyproject.toml
git commit -m "chore(mypy): include monitor.py in the type-check scope"
```

---

## Phase 6: Final verification (4 tasks)

### Task 6.1: Run all tests

```bash
cd "C:/Dev/Embedded/projects/wifi-scanner-signal-map"
pio test -e native
python -m pytest tests/ -q
```

**Expected:** Both exit 0. Capture the output for the changelog.

### Task 6.2: Verify the schema check still passes

```bash
python tools/check_schema.py
```

**Expected:** Exits 0. The schema is unchanged; this confirms the
firmware header and the Python schema are still in sync.

### Task 6.3: Run mypy (CI gate)

The CI workflow (`.github/workflows/ci.yml:39`) runs `mypy` over the
whole repo on every push. The new modules `wifiscan/timeseries.py` and
`monitor.py` MUST pass type-checking before merge, otherwise CI fails
silently from a local developer's perspective. Task 5.5 added
`monitor.py` to the scope; both modules must now appear in mypy's
output for the gate to mean anything.

```bash
cd "C:/Dev/Embedded/projects/wifi-scanner-signal-map"
python -m mypy
```

**Expected:** Exits 0 with no errors. The output must mention
`monitor.py` and `wifiscan/timeseries.py` (proof that they are
in scope). If either file is silently absent from the output, the
scope configuration has regressed and this gate is not actually
verifying anything.

### Task 6.4: Verify the firmware builds (no upload)

```bash
pio run
```

**Expected:** `SUCCESS` with no warnings.

---

## Issue tracking (separate, not part of the build)
The five deferred issues are already open on GitHub:

- Monitor deferred: presence — issue #30
- Monitor deferred: channel — issue #33
- Monitor deferred: drift — issue #34
- Multi-receiver triangulation (out of scope) — issue #32
- `!monitor` / `!promisc` radio conflict — issue #31

Each issue body references the relevant section of
`docs/plans/2026-06-25-radio-monitor-design.md`.

---

## Definition of Done

- [ ] `pio test -e native` is green
- [ ] `python -m pytest tests/ -q` is green
- [ ] `python tools/check_schema.py` is green
- [ ] `pio run` compiles the firmware without warnings
- [ ] `python monitor.py logs/<any>.csv` writes a valid PNG
- [ ] `python monitor.py ... --kind presence` raises
      `NotImplementedError` with the documented message
- [ ] `docs/monitor-deferred.md` exists
- [ ] `README.md` has the Monitor mode section
- [ ] `AGENTS.md` documents the `!monitor` / `!promisc` conflict
- [ ] All commits are on a single feature branch (e.g. `feature/radio-monitor`)
- [ ] `python -m mypy` is green (matches CI gate)
