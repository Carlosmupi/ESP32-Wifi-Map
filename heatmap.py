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
import sys
from pathlib import Path

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


def plot_scatter_heatmap(df: pd.DataFrame, ssid: str, out: Path) -> None:
    sub = df[df["ssid"] == ssid].copy()
    sub["rssi"] = sub["rssi"].astype(int)
    sub["est_distance_m"] = sub["est_distance_m"].astype(float)

    fig, ax = plt.subplots(figsize=(8, 6))
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
    ax.set_title(f"{ssid} — RSSI by spot")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.grid(True, alpha=0.3)
    cb = plt.colorbar(sc, ax=ax)
    cb.set_label("RSSI (dBm)")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def plot_bar_chart(df: pd.DataFrame, ssid: str, out: Path) -> None:
    sub = df[df["ssid"] == ssid].copy()
    sub["rssi"] = sub["rssi"].astype(int)
    sub["est_distance_m"] = sub["est_distance_m"].astype(float)

    # Sort by strongest signal at the top.
    sub = sub.sort_values("rssi", ascending=False)

    fig, ax = plt.subplots(figsize=(8, max(3, 0.4 * len(sub))))
    bars = ax.barh(sub["spot_label"], sub["rssi"],
                   color="#4a90e2", edgecolor="black")
    ax.set_xlim(RSSI_VMIN, RSSI_VMAX)
    ax.set_xlabel("RSSI (dBm)")
    ax.set_title(f"{ssid} — signal strength by spot")
    ax.invert_yaxis()
    ax.grid(True, axis="x", alpha=0.3)

    for bar, dist in zip(bars, sub["est_distance_m"]):
        ax.text(
            bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
            f"  {dist:.1f} m", va="center", fontsize=9, color="dimgray",
        )
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
    args = parser.parse_args()

    if not args.csv_path.is_file():
        raise SystemExit(f"CSV not found: {args.csv_path}")

    df = pd.read_csv(args.csv_path)
    required = {"spot_id", "spot_label", "ssid", "rssi", "est_distance_m"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"CSV missing required columns: {sorted(missing)}")

    coords = None
    if args.coords is not None:
        if not args.coords.is_file():
            raise SystemExit(f"coords file not found: {args.coords}")
        coords = pd.read_csv(args.coords)
        coord_cols = {"spot_label", "x", "y"}
        if not coord_cols.issubset(coords.columns):
            raise SystemExit(
                f"coords file must contain columns {sorted(coord_cols)}")
        df = df.merge(coords[["spot_label", "x", "y"]], on="spot_label",
                      how="left")
        if df[["x", "y"]].isna().any().any():
            nan_labels = sorted(
                df.loc[df["x"].isna() | df["y"].isna(), "spot_label"]
                .unique())
            print(f"[heatmap] WARNING: no coords for labels {nan_labels}; "
                  "they will be skipped from the scatter plot.")
            df = df.dropna(subset=["x", "y"])

    out_dir = args.csv_path.parent
    basename = args.csv_path.stem

    print(f"[heatmap] {len(df)} rows across "
          f"{df['spot_label'].nunique()} spot(s) and "
          f"{df['ssid'].nunique()} AP(s)")

    # Per-SSID summary.
    summary = (
        df.groupby("ssid")
        .agg(
            spots=("spot_label", "nunique"),
            strongest_rssi=("rssi", "max"),
            closest_m=("est_distance_m", "min"),
            closest_spot=("spot_label",
                          lambda s: s.loc[df.loc[s.index,
                                                 "est_distance_m"].idxmin()]),
        )
        .sort_values("strongest_rssi", ascending=False)
    )
    print("\n[heatmap] per-AP summary:")
    print(summary.to_string())

    # One plot per unique SSID.
    for ssid in sorted(df["ssid"].unique()):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in ssid)
        if not safe:
            safe = "hidden"
        if coords is not None:
            out = out_dir / f"{basename}_{safe}_heatmap.png"
            plot_scatter_heatmap(df, ssid, out)
        else:
            out = out_dir / f"{basename}_{safe}_bars.png"
            plot_bar_chart(df, ssid, out)
        print(f"[heatmap] wrote {out.name}")


if __name__ == "__main__":
    main()
