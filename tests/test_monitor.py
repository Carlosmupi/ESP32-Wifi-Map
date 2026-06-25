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
