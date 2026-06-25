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
    SCHEMA_VERSION,
    _safe_field,
    check_header,
    parse_data_row,
    parse_footer,
    parse_schema_version,
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


# ---------------------------------------------------------------------------
# _safe_field — CSV injection mitigation (issue #18)
# ---------------------------------------------------------------------------

def test_safe_field_equals_prefix() -> None:
    """A cell beginning with '=' gets a leading single-quote prefix."""
    assert _safe_field("=HYPERLINK(\"http://evil\",\"x\")") == "'=HYPERLINK(\"http://evil\",\"x\")"


def test_safe_field_plus_minus_at_prefix() -> None:
    """Cells beginning with '+', '-', or '@' get a leading single-quote prefix."""
    assert _safe_field("+cmd") == "'+cmd"
    assert _safe_field("-1+1|cmd") == "'-1+1|cmd"
    assert _safe_field("@SUM(A1:A2)") == "'@SUM(A1:A2)"


def test_safe_field_normal_unchanged() -> None:
    """Normal cells (no formula prefix) pass through untouched."""
    assert _safe_field("MyNet") == "MyNet"
    assert _safe_field("WPA2_PSK") == "WPA2_PSK"
    assert _safe_field("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"
    # A minus sign in the middle (not the first char) is fine.
    assert _safe_field("Net-5G") == "Net-5G"
    # Empty string is unchanged (no prefix).
    assert _safe_field("") == ""


def test_parse_data_row_applies_safe_field_to_injected_ssid() -> None:
    """parse_data_row() must run free-text fields through _safe_field()."""
    # SSID beginning with '=' — the canonical CSV-injection payload.  The
    # SSID contains commas and quotes, so it is CSV-quoted with doubled
    # internal quotes; csv.reader unescapes it back to the raw SSID before
    # _safe_field prefixes it.
    line = (
        '1,living-room,12345,"=HYPERLINK(""http://evil"",""x"")",'
        'aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK,2.34'
    )
    row = parse_data_row(line)
    assert row is not None
    assert row["ssid"] == '\'=HYPERLINK("http://evil","x")'
    # A non-injected free-text field is unchanged.
    assert row["auth_mode"] == "WPA2_PSK"
    # A numeric field is left untouched (no leading-quote corruption).
    assert row["rssi"] == "-55"


def test_parse_data_row_applies_safe_field_to_at_prefixed_label() -> None:
    """A spot_label beginning with '@' is also mitigated; numeric rssi is not."""
    line = (
        '1,@evil-label,12345,MyNet,aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK,2.34'
    )
    row = parse_data_row(line)
    assert row is not None
    assert row["spot_label"] == "'@evil-label"
    assert row["ssid"] == "MyNet"
    # Negative RSSI must NOT be prefixed — it is firmware-controlled and
    # prefixing would corrupt downstream numeric parsing.
    assert row["rssi"] == "-55"


# ---------------------------------------------------------------------------
# Schema version handshake (issue #16)
# ---------------------------------------------------------------------------

def test_schema_version_constant_is_one() -> None:
    """The canonical schema version is pinned to 1 (issue #16 baseline)."""
    assert SCHEMA_VERSION == 1


def test_parse_schema_version_well_formed() -> None:
    """A '# schema_version=N' line yields the integer N."""
    assert parse_schema_version("# schema_version=1") == 1
    assert parse_schema_version("# schema_version=42") == 42


def test_parse_schema_version_tolerates_extra_whitespace() -> None:
    """A single space after '#' and trailing whitespace still match."""
    assert parse_schema_version("#  schema_version=1") == 1
    assert parse_schema_version("# schema_version=1  ") == 1


def test_parse_schema_version_not_a_version_line() -> None:
    """Non-version lines (header, footer, data row) return None."""
    assert parse_schema_version(HEADER_LINE) is None
    assert parse_schema_version("# spot=1 label=x ap_count=2 scan_ms=3") is None
    assert parse_schema_version("1,living-room,12345,MyNet,aa:bb,-55,6,WPA2,2.34") is None
    assert parse_schema_version("") is None
    assert parse_schema_version("# schema_version=abc") is None


def test_version_check_message_match() -> None:
    """Matching firmware/capture versions log an OK line."""
    msg = capture.version_check_message(SCHEMA_VERSION)
    assert msg == f"[capture] schema_version={SCHEMA_VERSION}"
    assert "WARNING" not in msg


def test_version_check_message_mismatch_warns_but_continues() -> None:
    """A mismatch logs a WARNING but the message does not signal abort."""
    msg = capture.version_check_message(99, expected=SCHEMA_VERSION)
    assert msg.startswith("[capture] WARNING: schema_version mismatch")
    assert "99" in msg and str(SCHEMA_VERSION) in msg
    assert "continuing anyway" in msg


def test_version_check_message_legacy_missing_line() -> None:
    """No version line (legacy firmware) yields a one-line warning."""
    msg = capture.version_check_message(None, expected=SCHEMA_VERSION)
    assert msg.startswith("[capture] WARNING: no schema_version line seen")
    assert str(SCHEMA_VERSION) in msg
    # One line only — no embedded newlines.
    assert "\n" not in msg


def test_version_check_message_default_expected_matches_schema() -> None:
    """The default ``expected`` argument is wifiscan.schema.SCHEMA_VERSION."""
    assert capture.version_check_message(SCHEMA_VERSION) == \
        capture.version_check_message(SCHEMA_VERSION, SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# End-to-end-ish handshake simulation through capture's boot-line handling.
# Drives the same pre-header logic the real main loop uses, without a
# serial port: feed boot lines one at a time and confirm the version is
# captured and the right message is produced when the header arrives.
# ---------------------------------------------------------------------------

def _simulate_boot_handshake(boot_lines: list[str]) -> tuple[bool, int | None, str]:
    """Replay capture.py's pre-header logic over a list of boot lines.

    Returns ``(header_seen, fw_version, version_msg)`` mirroring what the
    real main loop would produce.  This exercises the same
    ``parse_schema_version`` + ``HEADER_LINE`` + ``version_check_message``
    calls the live loop makes, so a regression in any of them fails here.
    """
    header_seen = False
    fw_schema_version: int | None = None
    version_msg = ""
    for line in boot_lines:
        if not line:
            continue
        if not header_seen and fw_schema_version is None:
            v = parse_schema_version(line)
            if v is not None:
                fw_schema_version = v
        if not header_seen:
            if line == HEADER_LINE:
                header_seen = True
                version_msg = capture.version_check_message(fw_schema_version)
            continue
    return header_seen, fw_schema_version, version_msg


def test_boot_handshake_version_then_header() -> None:
    """Firmware prints '# schema_version=1' then the column header."""
    boot = [
        "# Wi-Fi Scanner with Signal Map",
        "# fw_version=0.2.0",
        "# schema_version=1",
        HEADER_LINE,
    ]
    seen, fw, msg = _simulate_boot_handshake(boot)
    assert seen is True
    assert fw == 1
    assert msg == "[capture] schema_version=1"


def test_boot_handshake_legacy_no_version_line() -> None:
    """Legacy firmware omits the version line; header still seen, warning emitted."""
    boot = [
        "# Wi-Fi Scanner with Signal Map",
        HEADER_LINE,
    ]
    seen, fw, msg = _simulate_boot_handshake(boot)
    assert seen is True
    assert fw is None
    assert msg.startswith("[capture] WARNING: no schema_version line seen")


def test_boot_handshake_version_mismatch_continues() -> None:
    """A future firmware advertising version 2 warns but still accepts the header."""
    boot = [
        "# schema_version=2",
        HEADER_LINE,
    ]
    seen, fw, msg = _simulate_boot_handshake(boot)
    assert seen is True
    assert fw == 2
    assert msg.startswith("[capture] WARNING: schema_version mismatch")
    assert "continuing anyway" in msg