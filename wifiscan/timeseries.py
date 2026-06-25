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
