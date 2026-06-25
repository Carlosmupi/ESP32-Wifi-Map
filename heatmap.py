#!/usr/bin/env python3
"""
heatmap.py — per-SSID signal-strength visualization for the Wi-Fi Scanner.

Reads a CSV produced by `capture.py` and renders one plot per unique SSID.

Two modes:
  1. With --coords coords.csv (columns: spot_label,x,y):
     A 2-D scatter heatmap showing RSSI at each measured position.
     Each point is annotated with its spot label and est_distance_m.
  2. Without coords:
     A horizontal bar chart of RSSI per spot, with est_distance_m overlay.

Usage:
    python heatmap.py logs/signal_map_20260624_120000.csv
    python heatmap.py logs/signal_map_20260624_120000.csv --coords coords.csv

Dependencies:
    pip install pandas matplotlib
"""

import argparse
import math
import sys
from pathlib import Path
# Canonical CSV schema (column list, filename sanitizer) lives in the
# wifiscan package so heatmap.py and capture.py agree.
from wifiscan.schema import EXPECTED_COLUMNS, safe_fieldname

try:
    import pandas as pd
except ImportError as exc:
    raise SystemExit(
        "pandas is required. Install with: pip install pandas"
    ) from exc

try:
    import matplotlib
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt
except ImportError as exc:
    raise SystemExit(
        "matplotlib is required. Install with: pip install matplotlib"
    ) from exc


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------
RSSI_VMIN = -90
RSSI_VMAX = -30
DISTANCE_CAP_M = 10.0  # must match the cap applied by wifi_scan_util.rssiToDistance()


def _coerce_numeric(df: pd.DataFrame, ssid: str) -> tuple[pd.DataFrame, list[str]]:
    """Coerce rssi and est_distance_m on the per-SSID subset.

    Non-numeric rssi is replaced with the -100 dBm sentinel (no signal);
    non-numeric est_distance_m is replaced with DISTANCE_CAP_M. The row is
    preserved so the spot is still annotated, and a one-line warning is
    emitted per coerced row so silent data loss is impossible.
    """
    sub = df[df["ssid"] == ssid].copy()
    warnings: list[str] = []

    rssi_num = pd.to_numeric(sub["rssi"], errors="coerce")
    rssi_bad = rssi_num.isna()
    if rssi_bad.any():
        for label in sub.loc[rssi_bad, "spot_label"].tolist():
            warnings.append(
                f"[heatmap] WARNING: rssi for spot '{label}' "
                f"(ssid='{ssid}') is not numeric; using sentinel -100 dBm"
            )
    sub["rssi"] = rssi_num.fillna(-100).astype(int)

    dist_num = pd.to_numeric(sub["est_distance_m"], errors="coerce")
    dist_bad = dist_num.isna()
    if dist_bad.any():
        for label in sub.loc[dist_bad, "spot_label"].tolist():
            warnings.append(
                f"[heatmap] WARNING: est_distance_m for spot '{label}' "
                f"(ssid='{ssid}') is not numeric; using sentinel "
                f"{DISTANCE_CAP_M} m"
            )
    sub["est_distance_m"] = dist_num.fillna(DISTANCE_CAP_M).astype(float)

    return sub, warnings


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    """Per-AP summary aggregation: spots seen, strongest RSSI, closest spot.

    Tie-breaking follows pandas ``idxmin`` behaviour: the first-encountered
    minimum wins.  Sorted by strongest RSSI descending.
    """
    grouped = df.groupby("ssid")

    agg = grouped.agg(
        spots=("spot_label", "nunique"),
        strongest_rssi=("rssi", "max"),
        closest_m=("est_distance_m", "min"),
    )
    # Pick the spot_label of the row with the minimum est_distance_m in
    # each group. idxmin() returns the first occurrence on ties, matching
    # the previous lambda-based behaviour. Done outside .agg() so the
    # lookup uses only the group's own data, with no closure over the
    # outer DataFrame (the prior implementation coupled the aggregation
    # to the caller's index structure and would break if pandas reset
    # the per-group Series index when invoking a custom agg lambda).
    agg["closest_spot"] = [
        g.loc[g["est_distance_m"].idxmin(), "spot_label"]
        for _, g in grouped
    ]
    return agg.sort_values("strongest_rssi", ascending=False)


def validate_required_columns(df: pd.DataFrame) -> None:
    """Raise SystemExit if any expected column is missing from df."""
    required = set(EXPECTED_COLUMNS)
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"CSV missing required columns: {sorted(missing)}")


def normalize_ssids(df: pd.DataFrame) -> None:
    """Replace empty/NaN SSIDs with 'hidden' in-place."""
    df["ssid"] = df["ssid"].fillna("hidden").replace("", "hidden")


def load_and_validate_coords(coords_path: Path) -> pd.DataFrame:
    """Load coords.csv, validate columns and numeric x/y.

    Raises SystemExit if the file is missing, lacks required columns, or
    contains non-numeric x/y values (a hard error to prevent silent
    scatter at (0, 0)).
    """
    if not coords_path.is_file():
        raise SystemExit(f"coords file not found: {coords_path}")
    coords = pd.read_csv(coords_path)
    coord_cols = {"spot_label", "x", "y"}
    if not coord_cols.issubset(coords.columns):
        raise SystemExit(
            f"coords file must contain columns {sorted(coord_cols)}")
    x_num = pd.to_numeric(coords["x"], errors="coerce")
    y_num = pd.to_numeric(coords["y"], errors="coerce")
    bad = (x_num.isna() & coords["x"].notna()) | (
        y_num.isna() & coords["y"].notna())
    if bad.any():
        offenders = []
        for idx in coords.index[bad]:
            offenders.append(
                f"spot_label={coords.at[idx, 'spot_label']!r} "
                f"x={coords.at[idx, 'x']!r} y={coords.at[idx, 'y']!r}"
            )
        raise SystemExit(
            "coords file contains non-numeric x/y values; offending "
            f"row(s): {'; '.join(offenders)}"
        )
    coords["x"] = x_num.astype(float)
    coords["y"] = y_num.astype(float)
    return coords


def merge_coords(df: pd.DataFrame, coords: pd.DataFrame) -> pd.DataFrame:
    """Left-merge coords onto df, dropping rows without coords with a warning."""
    df = df.merge(coords[["spot_label", "x", "y"]], on="spot_label",
                  how="left")
    if df[["x", "y"]].isna().any().any():
        nan_labels = sorted(
            df.loc[df["x"].isna() | df["y"].isna(), "spot_label"]
            .unique())
        for label in nan_labels:
            print(f"[heatmap] WARNING: no coords for spot '{label}'; "
                  "dropped from scatter plot")
        df = df.dropna(subset=["x", "y"])
    return df


def _draw_scatter(ax: "matplotlib.axes.Axes", sub: pd.DataFrame) -> None:
    """Draw a scatter heatmap onto an existing axes for one SSID's rows."""
    sc = ax.scatter(
        sub["x"], sub["y"],
        c=sub["rssi"], cmap="viridis",
        vmin=RSSI_VMIN, vmax=RSSI_VMAX,
        s=200, edgecolors="black", linewidths=0.5,
    )
    for _, row in sub.iterrows():
        ax.annotate(
            f"{row['spot_label']}\n{row['est_distance_m']:.1f}m",
            (row["x"], row["y"]),
            textcoords="offset points", xytext=(8, 8),
            fontsize=9, color="black",
        )
    ax.set_title(f"{sub['ssid'].iloc[0]} — RSSI by spot")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.grid(True, alpha=0.3)
    cb = plt.colorbar(sc, ax=ax)
    cb.set_label("RSSI (dBm)")


def _draw_bar(ax: "matplotlib.axes.Axes", sub: pd.DataFrame) -> None:
    """Draw a horizontal bar chart onto an existing axes for one SSID's rows."""
    sub = sub.sort_values("rssi", ascending=False)
    bars = ax.barh(sub["spot_label"], sub["rssi"],
                   color="#4a90e2", edgecolor="black")
    ax.set_xlim(RSSI_VMIN, RSSI_VMAX)
    ax.set_xlabel("RSSI (dBm)")
    ax.set_title(f"{sub['ssid'].iloc[0]} — signal strength by spot")
    ax.invert_yaxis()
    ax.grid(True, axis="x", alpha=0.3)
    for bar, dist in zip(bars, sub["est_distance_m"]):
        ax.text(
            bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
            f"  {dist:.1f} m", va="center", fontsize=9, color="dimgray",
        )


def plot_scatter_heatmap(df: pd.DataFrame, ssid: str, out: Path) -> None:
    sub, warnings = _coerce_numeric(df, ssid)
    for w in warnings:
        print(w)

    fig, ax = plt.subplots(figsize=(8, 6))
    _draw_scatter(ax, sub)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def plot_bar_chart(df: pd.DataFrame, ssid: str, out: Path) -> None:
    sub, warnings = _coerce_numeric(df, ssid)
    for w in warnings:
        print(w)

    fig, ax = plt.subplots(figsize=(8, max(3, 0.4 * len(sub))))
    _draw_bar(ax, sub)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def plot_combined(df: pd.DataFrame, ssids: list[str], out: Path,
                  has_coords: bool) -> None:
    """Render a single figure with a grid of subplots, one axes per SSID.

    Uses ``math.ceil(sqrt(len(ssids)))`` rows and columns. Each subplot
    reuses the same per-SSID drawing logic (scatter or bar) as the
    standalone plots. ``ssids`` is expected to be already filtered to the
    SSIDs present in ``df`` and within the combined-max threshold.
    """
    n = len(ssids)
    if n == 0:
        return
    ncols = math.ceil(math.sqrt(n))
    nrows = math.ceil(n / ncols)

    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(5 * ncols, 4 * nrows),
        squeeze=False,
    )
    axes_flat = axes.flatten()

    for idx, ssid in enumerate(ssids):
        ax = axes_flat[idx]
        sub, warnings = _coerce_numeric(df, ssid)
        for w in warnings:
            print(w)
        if has_coords:
            _draw_scatter(ax, sub)
        else:
            _draw_bar(ax, sub)

    # Hide any unused axes.
    for idx in range(n, len(axes_flat)):
        axes_flat[idx].set_visible(False)

    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path,
                        help="Captured CSV from capture.py")
    parser.add_argument("--coords", type=Path, default=None,
                        help="Optional coords.csv with columns "
                             "spot_label,x,y")
    parser.add_argument("--ssid", action="append", default=None,
                        metavar="NAME",
                        help="Render only the named SSID (repeatable). "
                             "A requested SSID absent from the data is "
                             "skipped with a printed warning.")
    parser.add_argument("--combined", action="store_true",
                        help="Render a single figure with a grid of "
                             "subplots (one axes per SSID) instead of one "
                             "PNG per SSID. Falls back to per-file output "
                             "when the SSID count exceeds --combined-max.")
    parser.add_argument("--combined-max", type=int, default=9,
                        metavar="N",
                        help="Maximum SSID count for which --combined "
                             "produces a single grid figure (default: 9).")
    args = parser.parse_args()

    if not args.csv_path.is_file():
        raise SystemExit(f"CSV not found: {args.csv_path}")

    df = pd.read_csv(args.csv_path)
    validate_required_columns(df)
    normalize_ssids(df)

    has_coords = False
    if args.coords is not None:
        coords = load_and_validate_coords(args.coords)
        df = merge_coords(df, coords)
        has_coords = True

    out_dir = args.csv_path.parent
    basename = args.csv_path.stem

    # --ssid filter: keep only requested SSIDs that exist in the data.
    if args.ssid is not None:
        available = set(df["ssid"].unique())
        requested = list(args.ssid)
        missing = [s for s in requested if s not in available]
        for s in missing:
            print(f"[heatmap] WARNING: requested SSID '{s}' not found in "
                  "data; skipping")
        df = df[df["ssid"].isin(requested)]

    print(f"[heatmap] {len(df)} rows across "
          f"{df['spot_label'].nunique()} spot(s) and "
          f"{df['ssid'].nunique()} AP(s)")

    if df.empty:
        print("[heatmap] no rows to plot after filtering; nothing written")
        return

    # Per-SSID summary.
    summary = summarise(df)
    print("\n[heatmap] per-AP summary:")
    print(summary.to_string())

    ssids = sorted(df["ssid"].unique())

    if args.combined and len(ssids) <= args.combined_max:
        safe = safe_fieldname("_".join(ssids)) if ssids else "combined"
        out = out_dir / f"{basename}_combined_{safe}.png"
        plot_combined(df, ssids, out, has_coords)
        print(f"[heatmap] wrote {out.name} ({len(ssids)} subplot(s))")
        return

    # Default: one plot per unique SSID.
    for ssid in ssids:
        safe = safe_fieldname(ssid)
        if has_coords:
            out = out_dir / f"{basename}_{safe}_heatmap.png"
            plot_scatter_heatmap(df, ssid, out)
        else:
            out = out_dir / f"{basename}_{safe}_bars.png"
            plot_bar_chart(df, ssid, out)
        print(f"[heatmap] wrote {out.name}")


if __name__ == "__main__":
    main()
