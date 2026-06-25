#!/usr/bin/env python3
"""check_schema.py — verify firmware_header.txt matches the Python schema.

The firmware prints a fixed CSV header at boot (see ``src/main.cpp``
``setup()``).  That string is the source of truth for the on-wire format.
``wifiscan.schema.HEADER_LINE`` derives from ``wifiscan.schema.EXPECTED_COLUMNS``
on the Python side; this script reifies the firmware's header into a
checked-in file (``firmware_header.txt``) and diffs the two.

CI can run this in pure Python — no ESP32 required.

Usage::

    python tools/check_schema.py
    python tools/check_schema.py --firmware-header path/to/header.txt
    python tools/check_schema.py --quiet   # exit code only

Exit codes:
    0 — header matches
    1 — header does not match (diff printed to stderr)
    2 — could not read the firmware header file
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _add_repo_to_path() -> None:
    """Make ``wifiscan`` importable regardless of the caller's cwd."""
    repo_root = str(REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def _read_header_line(path: Path) -> str:
    """Return the firmware header line from ``path``.

    The file may contain leading ``#``-prefixed comment lines (purpose,
    regeneration instructions) and a ``# schema_version=N`` line.  The
    header itself is the LAST non-empty line in the file — the canonical
    schema line.
    """
    text = path.read_text(encoding="utf-8")
    lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
    # Drop trailing blanks
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise ValueError(f"{path} is empty; expected a header line.")
    return lines[-1]


def _read_schema_version(path: Path) -> int | None:
    """Return the ``N`` from a ``# schema_version=N`` line in ``path``.

    Returns ``None`` if no such line is present.
    """
    _add_repo_to_path()
    from wifiscan.schema import parse_schema_version  # noqa: E402

    text = path.read_text(encoding="utf-8")
    for ln in text.splitlines():
        v = parse_schema_version(ln)
        if v is not None:
            return v
    return None


def _render_diff(expected: str, actual: str, firmware_header: Path) -> str:
    """Render a unified diff between the Python schema and the on-disk header."""
    diff_lines = difflib.unified_diff(
        expected.splitlines(),
        actual.splitlines(),
        fromfile="wifiscan.schema.HEADER_LINE",
        tofile=str(firmware_header),
        lineterm="",
    )
    return "\n".join(diff_lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diff the firmware's printed CSV header (firmware_header.txt) "
            "against the Python schema (wifiscan.schema.HEADER_LINE). "
            "Exits 0 if they match, 1 otherwise."
        ),
    )
    parser.add_argument(
        "--firmware-header",
        type=Path,
        default=REPO_ROOT / "firmware_header.txt",
        help="Path to the checked-in copy of the firmware's header line "
             "(default: %(default)s).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-error output; exit code only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    _add_repo_to_path()
    from wifiscan import schema  # noqa: E402  (import after sys.path tweak)

    expected = schema.HEADER_LINE
    expected_version = schema.SCHEMA_VERSION
    try:
        actual = _read_header_line(args.firmware_header)
    except FileNotFoundError:
        if not args.quiet:
            print(
                f"error: firmware header file not found: {args.firmware_header}",
                file=sys.stderr,
            )
        return 2

    exit_code = 0

    # 1. Header line match.
    if actual == expected:
        if not args.quiet:
            print(
                f"ok: {args.firmware_header} matches wifiscan.schema.HEADER_LINE "
                f"({len(schema.EXPECTED_COLUMNS)} columns)"
            )
    else:
        exit_code = 1
        if not args.quiet:
            print(
                f"error: {args.firmware_header} does not match "
                f"wifiscan.schema.HEADER_LINE.",
                file=sys.stderr,
            )
            print(_render_diff(expected, actual, args.firmware_header), file=sys.stderr)
            print(
                "\nFix: update wifiscan/schema.py's EXPECTED_COLUMNS, or "
                "regenerate firmware_header.txt via "
                "`python tools/sync_firmware_header.py`.",
                file=sys.stderr,
            )

    # 2. schema_version line match.
    actual_version = _read_schema_version(args.firmware_header)

    if actual_version is None:
        exit_code = 1
        if not args.quiet:
            print(
                f"error: {args.firmware_header} is missing a "
                f"'# schema_version=N' line (expected version "
                f"{expected_version}).",
                file=sys.stderr,
            )
    elif actual_version != expected_version:
        exit_code = 1
        if not args.quiet:
            print(
                f"error: {args.firmware_header} schema_version={actual_version} "
                f"does not match wifiscan.schema.SCHEMA_VERSION="
                f"{expected_version}.",
                file=sys.stderr,
            )
    else:
        if not args.quiet:
            print(
                f"ok: {args.firmware_header} schema_version={actual_version} "
                f"matches wifiscan.schema.SCHEMA_VERSION"
            )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
