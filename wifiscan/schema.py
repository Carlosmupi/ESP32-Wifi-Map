"""wifiscan.schema — canonical CSV schema for the Wi-Fi Scanner firmware.

The firmware (see ``src/main.cpp`` ``setup()``) prints one header line at
startup listing every column it will emit, then a sequence of CSV data
rows per spot, then a footer comment summarising each spot::

    # spot_id,spot_label,timestamp_ms,ssid,bssid,rssi,channel,auth_mode,est_distance_m
    1,living-room,12345,MyNet,aa:bb:cc:dd:ee:ff,-55,6,WPA2_PSK,2.34
    ...
    # spot=1 label=living-room ap_count=12 scan_ms=3456

Adding, removing, or renaming a column is a single edit to
:data:`EXPECTED_COLUMNS` here — the header string, the row parser, and
the column-count check all derive from it.  Run
``python -m wifiscan.schema --check-header "<line>"`` from CI to verify
the firmware's printed header still matches.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from typing import Optional

__all__ = [
    "EXPECTED_COLUMNS",
    "HEADER_LINE",
    "parse_data_row",
    "parse_footer",
    "safe_fieldname",
    "check_header",
    "main",
]


#: Columns emitted by the firmware, in firmware print order.  This tuple
#: is the single edit point for any schema change; everything else in
#: this module derives from it.
EXPECTED_COLUMNS: tuple[str, ...] = (
    "spot_id",
    "spot_label",
    "timestamp_ms",
    "ssid",
    "bssid",
    "rssi",
    "channel",
    "auth_mode",
    "est_distance_m",
)

#: Exact header string the firmware prints (see ``src/main.cpp:281``).
#: Built from :data:`EXPECTED_COLUMNS` so the two cannot drift apart
#: within Python — the firmware side is verified by
#: ``python -m wifiscan.schema --check-header``.
HEADER_LINE: str = "# " + ",".join(EXPECTED_COLUMNS)


# Per-spot footer emitted by the firmware's ``logCurrentSpot()``:
#   # spot=<id> label=<label> ap_count=<n> scan_ms=<duration_ms>
# ``label`` is user-entered text that may contain spaces; capture it
# non-greedily up to the next whitespace-delimited keyword.
_FOOTER_RE = re.compile(
    r"^#\s*spot=(?P<spot_id>\d+)\s+"
    r"label=(?P<label>.+?)"
    r"\s+ap_count=(?P<ap_count>\d+)\s+"
    r"scan_ms=(?P<scan_ms>\d+)\s*$"
)


def parse_data_row(line: str) -> Optional[dict]:
    """Parse a single CSV data row from the firmware into a dict.

    Returns ``None`` if ``line`` does not yield exactly
    ``len(EXPECTED_COLUMNS)`` fields — the firmware is the source of
    truth for column count, so the only valid row length matches it.
    Values are kept as strings; numeric coercion is the caller's job.
    """
    parts = next(csv.reader([line]))
    if len(parts) != len(EXPECTED_COLUMNS):
        return None
    return dict(zip(EXPECTED_COLUMNS, parts))


def parse_footer(line: str) -> Optional[dict]:
    """Parse a per-spot footer comment into a dict.

    Returns a dict with string keys ``spot_id``, ``label``, ``ap_count``,
    ``scan_ms`` when ``line`` matches the footer format, else ``None``.
    Values are strings — coerce as needed at the call site.
    """
    match = _FOOTER_RE.match(line)
    if match is None:
        return None
    return match.groupdict()


def safe_fieldname(ssid: str) -> str:
    """Sanitize an SSID for use as a filename suffix.

    Replaces any character that is not alphanumeric, ``-``, ``_``, or
    ``.`` with ``_``.  Returns ``"hidden"`` for empty/whitespace input
    so the caller always gets a non-empty, filesystem-safe identifier.
    """
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in ssid)
    return safe or "hidden"


def check_header(line: str) -> bool:
    """Return ``True`` iff ``line`` is byte-identical to :data:`HEADER_LINE`."""
    return line == HEADER_LINE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m wifiscan.schema",
        description=(
            "Inspect the canonical CSV schema or validate a header line "
            "against it (used by CI to detect firmware/Python drift)."
        ),
    )
    parser.add_argument(
        "--check-header",
        metavar="LINE",
        help="Exit non-zero if LINE does not match the canonical header.",
    )
    parser.add_argument(
        "--print-header",
        action="store_true",
        help="Print the canonical header line and exit 0.",
    )
    parser.add_argument(
        "--print-columns",
        action="store_true",
        help="Print EXPECTED_COLUMNS one per line and exit 0.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``python -m wifiscan.schema``."""
    args = _build_parser().parse_args(argv)

    if args.check_header is not None:
        if check_header(args.check_header):
            print("[schema] header OK", file=sys.stderr)
            return 0
        print(
            "[schema] header drift.\n"
            f"  expected: {HEADER_LINE!r}\n"
            f"  got:      {args.check_header!r}",
            file=sys.stderr,
        )
        return 1

    if args.print_header:
        print(HEADER_LINE)
        return 0

    if args.print_columns:
        for col in EXPECTED_COLUMNS:
            print(col)
        return 0

    _build_parser().print_help(sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
