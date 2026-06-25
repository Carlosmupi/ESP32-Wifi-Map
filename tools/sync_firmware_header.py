#!/usr/bin/env python3
"""sync_firmware_header.py — regenerate firmware_header.txt from src/main.cpp.

The firmware prints its CSV header inside an ``F("...")`` macro in
``src/main.cpp``'s ``setup()``.  CI can't flash the board to read that
string, so we reify it as a checked-in file that ``tools/check_schema.py``
diffs against the Python schema.  Run this script locally after editing
the firmware header line, then commit the regenerated ``firmware_header.txt``.

Usage::

    python tools/sync_firmware_header.py
    python tools/sync_firmware_header.py --src path/to/main.cpp \\
                                          --out path/to/firmware_header.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Match `Serial.println(F("# ... "));` and capture the F() string literal.
# `setup()` also calls `Serial.println(F("..."))` for other comment metadata
# (e.g. "# Wi-Fi Scanner with Signal Map"); the actual column header is the
# only one with a comma list, so we filter on that below.
_HEADER_RE = re.compile(
    r'Serial\.println\(\s*F\s*\(\s*"([^"]+)"\s*\)\s*\)\s*;',
)

HEADER_COMMENT = (
    "# Tracks the firmware's CSV header line (see src/main.cpp setup()); "
    "regenerate with `python tools/sync_firmware_header.py`.\n"
)


def _extract_header(src_text: str) -> str:
    """Find the firmware header line inside ``src_text``."""
    for match in _HEADER_RE.finditer(src_text):
        literal = match.group(1)
        if literal.startswith("# ") and "," in literal:
            return literal
    raise LookupError(
        "could not find a Serial.println(F(\"# ...\")) line with column "
        "commas in src/main.cpp — has the firmware header format changed?"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regenerate firmware_header.txt from src/main.cpp.",
    )
    parser.add_argument(
        "--src",
        type=Path,
        default=REPO_ROOT / "src" / "main.cpp",
        help="Path to the firmware main.cpp (default: %(default)s).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "firmware_header.txt",
        help="Path to the firmware_header.txt to write (default: %(default)s).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        src_text = args.src.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: source not found: {args.src}", file=sys.stderr)
        return 2

    try:
        header = _extract_header(src_text)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    args.out.write_text(HEADER_COMMENT + header + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(header)} chars): {header}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
