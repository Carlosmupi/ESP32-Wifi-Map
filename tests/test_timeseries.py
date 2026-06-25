"""Pytest suite for wifiscan.timeseries.

Covers the CSV->Timeseries load: row parsing, per-BSSID grouping, RSSI
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
