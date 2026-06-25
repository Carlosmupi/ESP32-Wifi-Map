"""pytest suite for capture.py and the wifiscan.schema helpers it depends on.

Wave-0 split the CSV schema out of capture.py into ``wifiscan.schema`` (issue #6)
so both capture.py and heatmap.py share a single source of truth.  This suite
covers both layers:

* The Wave-0 P0 silent-data-loss regression in :func:`capture.write_header`
  + :func:`capture.append_rows` — multi-spot data MUST persist to the file
  end-to-end (pre-fix, only the last spot survived).
* Header detection via ``HEADER_LINE`` / ``check_header``.
* Row/footer parsing via :func:`wifiscan.schema.parse_data_row` and
  :func:`wifiscan.schema.parse_footer`, including CSV-escaped SSIDs and
  malformed input.
* :func:`capture.timestamped_path` shape.
* :func:`wifiscan.schema.safe_fieldname` sanitization.

No real serial port is used; the suite drives the state-machine helpers
directly and uses ``tmp_path`` for all filesystem needs.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

# ``capture`` is a top-level script (not installed as a package) and
# ``wifiscan.schema`` lives one directory up.  Putting the project root on
# sys.path lets ``import capture`` and ``from wifiscan.schema import ...``
# resolve without editable-install gymnastics.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import capture  # noqa: E402  (after sys.path manipulation)
from wifiscan.schema import (  # noqa: E402
    EXPECTED_COLUMNS,
    HEADER_LINE,
    check_header,
    parse_data_row,
    parse_footer,
    safe_fieldname,
)

# capture.py raises SystemExit on import if pyserial is missing.  The schema
# helpers themselves do not need pyserial, but we exercise the same import
# path that users hit at runtime.  Skip the whole module if pyserial is not
# available so a missing optional dep doesn't fail unrelated CI runs.
pytest.importorskip("serial")


# ---------------------------------------------------------------------------
# Helpers (module-scoped, per the no-conftest.py rule)
# ---------------------------------------------------------------------------

def make_row(
    *,
    spot_id: str = "1",
    spot_label: str = "living-room",
    timestamp_ms: str = "12345",
    ssid: str = "MyNet",
    bssid: str = "aa:bb:cc:dd:ee:ff",
    rssi: str = "-55",
    channel: str = "6",
    auth_mode: str = "WPA2_PSK",
    est_distance_m: str = "2.34",
) -> dict:
    """Build a dict matching ``EXPECTED_COLUMNS`` for DictWriter-based append."""
    return {
        "spot_id": spot_id,
        "spot_label": spot_label,
        "timestamp_ms": timestamp_ms,
        "ssid": ssid,
        "bssid": bssid,
        "rssi": rssi,
        "channel": channel,
        "auth_mode": auth_mode,
        "est_distance_m": est_distance_m,
    }


# ---------------------------------------------------------------------------
# P0 regression — multi-spot persistence
# ---------------------------------------------------------------------------

def test_p0_regression_multi_spot_persistence(tmp_path: Path) -> None:
    """P0 regression: write_header() + append_rows() must keep every spot.

    Pre-fix (broken behaviour prior to commit 0a3e070), the output file was
    truncated on every spot footer, so only the LAST spot's rows survived on
    disk — silent data loss across an entire capture session.  Post-fix,
    ``write_header`` is called once and ``append_rows`` opens in append mode
    so every spot's data is preserved.

    This test pins the contract by driving the public surface directly, so
    future refactors cannot regress the bug without breaking CI.
    """
    out = tmp_path / "signal_map_p0_regression.csv"

    spot1 = [
        make_row(spot_id="1", ssid="Net1", rssi="-55"),
        make_row(spot_id="1", ssid="Net2", rssi="-60"),
    ]
    spot2 = [
        make_row(spot_id="2", ssid="Net1", rssi="-65"),
        make_row(spot_id="2", ssid="Net3", rssi="-70"),
        make_row(spot_id="2", ssid="Net4", rssi="-72"),
    ]
    spot3 = [
        make_row(spot_id="3", ssid="Net5", rssi="-80"),
    ]

    capture.write_header(out)
    capture.append_rows(out, spot1)
    capture.append_rows(out, spot2)
    capture.append_rows(out, spot3)

    text = out.read_text(encoding="utf-8")
    lines = text.splitlines()

    # On-disk header is the bare CSV column list (no '# ' prefix); the
    # canonical ``HEADER_LINE`` constant has the '# ' because that is the
    # *wire format* the firmware prints — ``csv.DictWriter.writeheader``
    # writes standard CSV without the comment marker.
    assert lines[0] == ",".join(EXPECTED_COLUMNS)
    # Total lines = header + 2 + 3 + 1 = 7.  Pre-fix this would have been
    # header + 1 (only the last spot survived).
    assert len(lines) == 7, (
        f"expected 7 lines (header + 6 data), got {len(lines)}: {lines!r}"
    )

    # Every spot's data must be on disk — the bug truncated earlier spots.
    data_lines = lines[1:]
    assert "Net1" in text and "Net2" in text
    assert "Net3" in text and "Net4" in text
    assert "Net5" in text

    # Per-spot row counts must match what we wrote — this is the strongest
    # assertion against silent truncation.
    spot1_lines = [l for l in data_lines if l.split(",")[0] == "1"]
    spot2_lines = [l for l in data_lines if l.split(",")[0] == "2"]
    spot3_lines = [l for l in data_lines if l.split(",")[0] == "3"]
    assert len(spot1_lines) == 2, f"spot 1 lost rows: {data_lines!r}"
    assert len(spot2_lines) == 3, f"spot 2 lost rows: {data_lines!r}"
    assert len(spot3_lines) == 1, f"spot 3 lost rows: {data_lines!r}"


# ---------------------------------------------------------------------------
# HEADER_LINE / check_header
# ---------------------------------------------------------------------------

def test_check_header_present_passes() -> None:
    """The canonical header string must match itself."""
    assert check_header(HEADER_LINE) is True


def test_check_header_missing_no_match() -> None:
    """An empty / unrelated line must not be accepted as the header."""
    assert check_header("") is False
    assert check_header("not a header line") is False
    assert check_header("spot_id,spot_label,timestamp_ms") is False  # missing leading '# '


def test_check_header_malformed_no_match() -> None:
    """A header-like line with a typo or wrong column order must be rejected."""
    # Wrong column name.
    bad_typo = HEADER_LINE.replace("spot_id", "spotX")
    assert check_header(bad_typo) is False
    # Drop one column.
    bad_short = HEADER_LINE.replace(",est_distance_m", "")
    assert check_header(bad_short) is False
    # Add an extra column.
    bad_long = HEADER_LINE + ",extra"
    assert check_header(bad_long) is False


# ---------------------------------------------------------------------------
# parse_data_row
# ---------------------------------------------------------------------------

def test_parse_data_row_well_formed() -> None:
    """A 9-field row maps cleanly to EXPECTED_COLUMNS."""
    line = "1,living-room,12345,MyNet,aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK,2.34"
    row = parse_data_row(line)
    assert row is not None
    assert row == make_row()
    assert set(row.keys()) == set(EXPECTED_COLUMNS)


def test_parse_data_row_escaped_ssid_with_comma() -> None:
    """SSID containing a comma is CSV-quoted; reader must unquote it."""
    line = (
        '1,living-room,12345,"My,Net",aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK,2.34'
    )
    row = parse_data_row(line)
    assert row is not None
    assert row["ssid"] == "My,Net"
    # Field count still 9 after unescaping.
    assert len(row) == len(EXPECTED_COLUMNS)


def test_parse_data_row_escaped_ssid_with_embedded_quote() -> None:
    """SSID containing a double-quote is escaped as '""' inside a quoted field."""
    line = (
        '1,living-room,12345,"My""Net",aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK,2.34'
    )
    row = parse_data_row(line)
    assert row is not None
    assert row["ssid"] == 'My"Net'
    assert len(row) == len(EXPECTED_COLUMNS)


def test_parse_data_row_wrong_arity_8() -> None:
    """An 8-field row is rejected — the firmware is the source of truth for length."""
    line = "1,living-room,12345,MyNet,aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK"
    assert parse_data_row(line) is None


def test_parse_data_row_wrong_arity_10() -> None:
    """A 10-field row is rejected — same reason."""
    line = (
        "1,living-room,12345,MyNet,aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK,2.34,oops"
    )
    assert parse_data_row(line) is None


def test_parse_data_row_empty_line() -> None:
    """An empty line yields no row (callers skip it without warning)."""
    assert parse_data_row("") is None


def test_parse_data_row_footer_shaped_line() -> None:
    """A '# spot=...' footer comment is NOT a data row — parser must say None."""
    footer_like = "# spot=1 label=living-room ap_count=12 scan_ms=3456"
    assert parse_data_row(footer_like) is None


# ---------------------------------------------------------------------------
# parse_footer
# ---------------------------------------------------------------------------

def test_parse_footer_full() -> None:
    """A complete footer line yields all four fields as strings."""
    line = "# spot=1 label=living-room ap_count=12 scan_ms=3456"
    parsed = parse_footer(line)
    assert parsed is not None
    assert parsed == {
        "spot_id": "1",
        "label": "living-room",
        "ap_count": "12",
        "scan_ms": "3456",
    }


def test_parse_footer_partial_missing_scan_ms() -> None:
    """A footer missing the scan_ms field is rejected."""
    line = "# spot=1 label=living-room ap_count=12"
    assert parse_footer(line) is None


def test_parse_footer_partial_missing_label() -> None:
    """A footer missing the label field is rejected (label is non-greedy but required)."""
    line = "# spot=1 ap_count=12 scan_ms=3456"
    assert parse_footer(line) is None


def test_parse_footer_not_a_footer() -> None:
    """A plain CSV data row is not a footer — parser returns None."""
    line = "1,living-room,12345,MyNet,aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK,2.34"
    assert parse_footer(line) is None


def test_parse_footer_trailing_garbage() -> None:
    """Extra tokens after scan_ms break the anchor and must be rejected."""
    line = "# spot=1 label=living-room ap_count=12 scan_ms=3456 garbage"
    assert parse_footer(line) is None


# ---------------------------------------------------------------------------
# timestamped_path
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point capture.OUTPUT_DIR at a per-test tmp dir so we never touch the real logs/."""
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    monkeypatch.setattr(capture, "OUTPUT_DIR", fake_logs)
    return fake_logs


def test_timestamped_path_format(isolated_output_dir: Path) -> None:
    """timestamped_path() yields '<parent>/logs/signal_map_<YYYYMMDD_HHMMSS>.csv'."""
    p = capture.timestamped_path()
    assert p.parent == isolated_output_dir
    assert p.parent.name == "logs"
    assert p.name.startswith("signal_map_")
    assert p.name.endswith(".csv")
    # Sanity: the timestamp portion is 15 chars ('YYYYMMDD_HHMMSS').
    suffix = p.name[len("signal_map_"):-len(".csv")]
    assert len(suffix) == 15
    assert suffix[8] == "_"


# ---------------------------------------------------------------------------
# safe_fieldname (now imported from wifiscan.schema)
# ---------------------------------------------------------------------------

def test_safe_fieldname_alnum_unchanged() -> None:
    """ASCII alphanumeric SSIDs pass through untouched."""
    assert safe_fieldname("MyNet123") == "MyNet123"
    assert safe_fieldname("home_wifi") == "home_wifi"
    assert safe_fieldname("NET-A.B") == "NET-A.B"


def test_safe_fieldname_special_chars_replaced() -> None:
    """Anything outside [A-Za-z0-9_.-] is collapsed to '_'."""
    assert safe_fieldname("My,Net") == "My_Net"
    assert safe_fieldname("home/wifi") == "home_wifi"
    assert safe_fieldname("with space") == "with_space"
    assert safe_fieldname("tab\there") == "tab_here"
    assert safe_fieldname("quote\"in") == "quote_in"


def test_safe_fieldname_empty_returns_hidden() -> None:
    """Empty SSID yields the sentinel 'hidden' so the caller always gets a name."""
    assert safe_fieldname("") == "hidden"