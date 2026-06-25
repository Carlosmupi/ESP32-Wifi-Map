"""wifiscan.merge — concatenate multiple capture CSVs into one.

Reads two or more capture CSV files (as written by ``capture.py``), offsets
each input's ``spot_id`` so the merged column is globally unique, optionally
deduplicates ``(spot_label, bssid, channel)`` triplets by median RSSI and
median ``est_distance_m``, sorts by ``(timestamp_ms, spot_id)``, and writes a
single merged CSV.

CLI::

    python -m wifiscan.merge logs/a.csv logs/b.csv
    python -m wifiscan.merge logs/a.csv logs/b.csv --output logs/merged.csv
    python -m wifiscan.merge logs/a.csv logs/b.csv --dedup median

The merged output uses the standard CSV header (no ``# `` prefix) written by
``csv.DictWriter`` / ``pandas.to_csv``, matching what ``heatmap.py`` reads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from wifiscan.schema import EXPECTED_COLUMNS

__all__ = ["merge_files", "dedup_median", "main"]


def merge_files(paths: list[Path], dedup: Optional[str] = None) -> pd.DataFrame:
    """Load every CSV in ``paths``, concat with ``spot_id`` offset, sort.

    Each input's ``spot_id`` is shifted by one plus the running maximum of
    the previously-accumulated inputs so the merged ``spot_id`` column has
    no collisions across files.  ``spot_label`` and ``timestamp_ms`` are
    preserved verbatim.  The result is sorted by ``(timestamp_ms, spot_id)``
    with a stable sort and reindexed.

    If ``dedup == "median"``, identical ``(spot_label, bssid, channel)``
    triplets are collapsed to one row carrying the median ``rssi`` and
    median ``est_distance_m`` of the group.
    """
    if not paths:
        raise SystemExit("merge: no input files given")

    frames: list[pd.DataFrame] = []
    spot_id_offset = 0
    for p in paths:
        df = pd.read_csv(p)
        missing = set(EXPECTED_COLUMNS) - set(df.columns)
        if missing:
            raise SystemExit(f"{p}: missing columns: {sorted(missing)}")
        df = df[list(EXPECTED_COLUMNS)].copy()
        df["spot_id"] = df["spot_id"].astype(int) + spot_id_offset
        spot_id_offset = int(df["spot_id"].max()) + 1
        frames.append(df)

    merged = pd.concat(frames, ignore_index=True)

    if dedup == "median":
        merged = dedup_median(merged)

    merged = merged.sort_values(
        ["timestamp_ms", "spot_id"], kind="stable"
    ).reset_index(drop=True)
    return merged


def dedup_median(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse ``(spot_label, bssid, channel)`` triplets via median.

    For each triplet group, ``rssi`` and ``est_distance_m`` are replaced by
    the group median.  Non-numeric columns (``ssid``, ``auth_mode``,
    ``timestamp_ms``, ``spot_id``) keep the first row of the group.  The
    returned DataFrame keeps the EXPECTED_COLUMNS order.
    """
    group_keys = ["spot_label", "bssid", "channel"]
    agg = df.groupby(group_keys, sort=False, as_index=False).agg(
        rssi=("rssi", "median"),
        est_distance_m=("est_distance_m", "median"),
        spot_id=("spot_id", "first"),
        timestamp_ms=("timestamp_ms", "first"),
        ssid=("ssid", "first"),
        auth_mode=("auth_mode", "first"),
        frame_type=("frame_type", "first"),
        src_mac=("src_mac", "first"),
    )
    return agg[list(EXPECTED_COLUMNS)]


def _default_output_path(first_input: Path) -> Path:
    """``<first_input_stem>_merged.csv`` next to the first input file."""
    return first_input.with_name(f"{first_input.stem}_merged.csv")


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``python -m wifiscan.merge``."""
    parser = argparse.ArgumentParser(
        prog="python -m wifiscan.merge",
        description=(
            "Concatenate multiple capture CSVs into one, offsetting spot_id "
            "to stay globally unique and sorting by (timestamp_ms, spot_id)."
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Two or more capture CSV files to merge.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output CSV path. Defaults to <first_input_stem>_merged.csv "
            "next to the first input file."
        ),
    )
    parser.add_argument(
        "--dedup",
        choices=["median"],
        default=None,
        help=(
            "Optional dedup mode. 'median' collapses identical "
            "(spot_label, bssid, channel) triplets, keeping median RSSI "
            "and median est_distance_m."
        ),
    )
    args = parser.parse_args(argv)

    if len(args.inputs) < 2:
        parser.error("merge requires at least two input files")

    for p in args.inputs:
        if not p.is_file():
            print(f"merge: input not found: {p}", file=sys.stderr)
            return 1

    merged = merge_files(args.inputs, dedup=args.dedup)

    out_path = args.output if args.output is not None else _default_output_path(args.inputs[0])
    merged.to_csv(out_path, index=False)

    print(
        f"merged {len(merged)} rows from {len(args.inputs)} file(s) into {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
