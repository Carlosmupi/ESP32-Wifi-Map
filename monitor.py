#!/usr/bin/env python3
"""
monitor.py - render a per-AP sparkline grid from a Wi-Fi Scanner CSV.

The CSV is the same one produced by `capture.py` while the firmware is
in `!monitor` mode. Each row in the CSV becomes one (timestamp, RSSI)
sample; the script groups samples by BSSID and draws a tiny line plot
per AP in a single grid figure.

Usage:
    python monitor.py path/to/session.csv
    python monitor.py path/to/session.csv --top 20
    python monitor.py path/to/session.csv --output out_dir/

Other `--kind` values (presence, channel, drift) are registered but
not yet implemented; see docs/monitor-deferred.md.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Callable, Dict, List, Tuple

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as exc:
    raise SystemExit(
        "matplotlib is required. Install with: pip install matplotlib"
    ) from exc

# Repo root on sys.path so `import wifiscan` works without an install.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wifiscan.timeseries import load_timeseries, Timeseries  # noqa: E402

__all__ = ["plot_sparkline", "dispatch", "PLOTTERS"]

#: Default visual range for the y-axis. Matches heatmap.py.
RSSI_VMIN = -95
RSSI_VMAX = -30

#: A safe filename for a BSSID. Falls back to the raw MAC if the
#: input cannot be sanitized (e.g. empty string after stripping).
def _safe_bssid(bssid: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in bssid)
    return cleaned or "unknown"


def _ranked_bssids(ts: Timeseries) -> List[Tuple[str, List[Tuple[int, int]]]]:
    """Return BSSIDs sorted by descending sample count, then BSSID.

    Each element of the result is ``(bssid, samples)`` where ``samples``
    is the full ordered list of ``(timestamp_ms, rssi)`` pairs for that
    BSSID (preserved from :data:`Timeseries`). The call site unpacks
    each sample as ``(t, r)`` - the annotation must therefore be
    ``List[Tuple[int, int]]`` per BSSID, not ``int``.
    """
    return sorted(ts.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _grid_dims(n: int) -> Tuple[int, int]:
    """Return (rows, cols) for a near-square grid of n panels."""
    if n <= 0:
        return 0, 0
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = math.ceil(n / cols)
    return rows, cols


def plot_sparkline(ts: Timeseries, out: Path, top: int = 12) -> None:
    """Write a per-BSSID sparkline grid to ``out``.

    Parameters
    ----------
    ts
        Output of :func:`wifiscan.timeseries.load_timeseries`.
    out
        Destination PNG path. Parent directory is created if missing.
    top
        Maximum number of BSSIDs to plot. The most-sampled BSSIDs win.
    """
    if not ts:
        raise SystemExit("monitor: empty timeseries; nothing to plot.")

    ranked = _ranked_bssids(ts)[: max(1, top)]
    rows, cols = _grid_dims(len(ranked))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.2, rows * 1.6),
                             squeeze=False)

    for ax, (bssid, samples) in zip(axes.flat, ranked):
        ts_ms = [t for t, _ in samples]
        rssi  = [r for _, r in samples]
        ax.plot(ts_ms, rssi, linewidth=0.9)
        ax.set_ylim(RSSI_VMIN, RSSI_VMAX)
        ax.set_title(_safe_bssid(bssid), fontsize=7, loc="left")
        ax.tick_params(axis="both", which="both", labelsize=6, length=0)
        ax.grid(True, linewidth=0.3, alpha=0.4)

    # Hide any unused axes (when the grid has more cells than BSSIDs).
    for ax in axes.flat[len(ranked):]:
        ax.set_visible(False)

    fig.suptitle("RSSI over time (per BSSID)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Deferred plot adapters
# ---------------------------------------------------------------------------

def _not_implemented(kind: str) -> None:
    raise NotImplementedError(
        f"monitor --kind {kind!r} is registered but not implemented yet. "
        "See docs/monitor-deferred.md for the design."
    )


def plot_presence(_ts: Timeseries, out: Path, top: int = 12) -> None:  # noqa: ARG001
    _not_implemented("presence")


def plot_channel(_ts: Timeseries, out: Path, top: int = 12) -> None:  # noqa: ARG001
    _not_implemented("channel")


def plot_drift(_ts: Timeseries, out: Path, top: int = 12) -> None:  # noqa: ARG001
    _not_implemented("drift")


#: Registry of --kind values to plot functions.
PLOTTERS: Dict[str, Callable[[Timeseries, Path, int], None]] = {
    "sparkline": plot_sparkline,
    "presence":  plot_presence,
    "channel":   plot_channel,
    "drift":     plot_drift,
}


def dispatch(kind: str, ts: Timeseries, out: Path, top: int) -> None:
    """Resolve ``--kind`` to a plot function and call it."""
    fn = PLOTTERS.get(kind)
    if fn is None:
        raise SystemExit(
            f"monitor: unknown --kind {kind!r}. "
            f"Known: {sorted(PLOTTERS)}"
        )
    fn(ts, out, top)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", type=Path, help="Path to a Wi-Fi Scanner CSV.")
    p.add_argument("--kind", choices=sorted(PLOTTERS), default="sparkline",
                   help="Visualization kind. Default: sparkline.")
    p.add_argument("--top", type=int, default=12,
                   help="Maximum BSSIDs to include. Default: 12.")
    p.add_argument("--output", type=Path, default=None,
                   help="Output directory. Default: alongside the CSV.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    ts = load_timeseries(args.csv)
    out_dir = args.output or args.csv.parent
    out_file = out_dir / f"{args.csv.stem}_{args.kind}.png"
    dispatch(args.kind, ts, out_file, args.top)
    print(f"[monitor] {len(ts)} BSSIDs -> {out_file.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
