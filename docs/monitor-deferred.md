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
iteration could deploy >=3 ESP32 boards with clock sync, each running
`!monitor`, and a host-side service that fuses their per-BSSID RSSI
streams into trilateralized device positions. This is a substantial
new project, not a feature of this tool.

Tracked in issue #32.
