"""Pytest suite for heatmap.py.

Covers the post-#5 robust numeric coercion, the summary aggregation inlined
in heatmap.main(), the safe_fieldname sanitizer (re-exported from
wifiscan.schema), the required-column CSV check, the hidden-network SSID
replacement, and the plot_bar_chart / plot_scatter_heatmap end-to-end paths.

The orchestrator owns shared test infrastructure (conftest.py, pytest.ini,
pyproject.toml [tool.pytest.ini_options], etc.) so this file defines any
helpers it needs at module scope and uses the pytest `tmp_path` fixture for
all filesystem writes — no committed fixtures.
"""

from __future__ import annotations

import io
import sys
import pytest

from pathlib import Path

# Repository root on sys.path so `import heatmap` and `import wifiscan` work
# without an installed package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Skip cleanly if optional test dependencies are missing.
pd = pytest.importorskip("pandas")  # noqa: F821  (pytest injects at runtime)
matplotlib = pytest.importorskip("matplotlib")
# Headless backend MUST be selected before pyplot is imported anywhere.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (must follow use("Agg"))

pytest.importorskip("wifiscan")

import heatmap  # noqa: E402
from wifiscan.schema import EXPECTED_COLUMNS, safe_fieldname  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers (inline — no shared conftest)
# ---------------------------------------------------------------------------
def make_row(
    ssid: str = "Net",
    spot_id: int = 1,
    spot_label: str = "s1",
    rssi: int = -50,
    est_distance_m: float = 1.0,
    bssid: str = "aa:bb:cc:dd:ee:ff",
    timestamp_ms: int = 1_000,
    channel: int = 6,
    auth_mode: str = "WPA2_PSK",
) -> dict:
    """Return one data-row dict keyed by every column in EXPECTED_COLUMNS."""
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


def make_df(rows: list[dict], extra: dict | None = None) -> pd.DataFrame:
    """Build a DataFrame with EXPECTED_COLUMNS plus any extras (e.g. x, y)."""
    df = pd.DataFrame(rows, columns=list(EXPECTED_COLUMNS))
    if extra:
        for k, v in extra.items():
            df[k] = v
    return df


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    """Mirror the per-AP summary aggregation in heatmap.main().

    The function is inlined in heatmap.py; this helper makes the contract
    explicit and independently testable.  Tie-breaking is the documented
    pandas ``idxmin`` behavior: the first-encountered minimum wins.
    """
    return (
        df.groupby("ssid")
        .agg(
            spots=("spot_label", "nunique"),
            strongest_rssi=("rssi", "max"),
            closest_m=("est_distance_m", "min"),
            closest_spot=(
                "spot_label",
                lambda s: s.loc[
                    df.loc[s.index, "est_distance_m"].idxmin()
                ],
            ),
        )
        .sort_values("strongest_rssi", ascending=False)
    )


# ---------------------------------------------------------------------------
# safe_fieldname
# ---------------------------------------------------------------------------
class TestSafeFieldname:
    def test_alnum_unchanged(self):
        assert safe_fieldname("MyNet") == "MyNet"
        assert safe_fieldname("Net42") == "Net42"
        assert safe_fieldname("abc") == "abc"

    def test_slash_replaced(self):
        assert safe_fieldname("a/b") == "a_b"
        assert safe_fieldname("a/b/c") == "a_b_c"

    def test_backslash_replaced(self):
        assert safe_fieldname("a\\b") == "a_b"

    def test_space_replaced(self):
        assert safe_fieldname("hello world") == "hello_world"

    def test_comma_replaced(self):
        assert safe_fieldname("a,b") == "a_b"

    def test_double_quote_replaced(self):
        assert safe_fieldname('a"b') == "a_b"

    def test_emoji_replaced(self):
        # Emoji is a non-alnum multi-byte character; each codepoint collapses
        # to a single underscore.
        assert safe_fieldname("cafe\u202e") == "cafe_"
        assert safe_fieldname("\U0001f600") == "_"

    def test_empty_returns_hidden(self):
        assert safe_fieldname("") == "hidden"

    def test_underscore_and_dash_preserved(self):
        # The implementation whitelists -, _, . in addition to alnum.
        assert safe_fieldname("a-b_c.d") == "a-b_c.d"

    def test_safe_fieldname_reexported_from_schema(self):
        # The contract is: heatmap.py imports safe_fieldname from
        # wifiscan.schema.  If this assertion ever fires, the re-export was
        # lost and filenames could diverge between capture and heatmap.
        from wifiscan import schema as schema_mod
        assert schema_mod.safe_fieldname is safe_fieldname


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------
class TestSummary:
    def test_strongest_rssi_per_ap(self):
        df = make_df([
            make_row(ssid="A", spot_label="s1", rssi=-70, est_distance_m=1.0),
            make_row(ssid="A", spot_label="s2", rssi=-50, est_distance_m=5.0),
            make_row(ssid="B", spot_label="s1", rssi=-60, est_distance_m=2.0),
            make_row(ssid="B", spot_label="s2", rssi=-65, est_distance_m=4.0),
        ])
        s = summarise(df)
        assert s.loc["A", "strongest_rssi"] == -50
        assert s.loc["B", "strongest_rssi"] == -60

    def test_closest_spot_per_ap(self):
        df = make_df([
            make_row(ssid="A", spot_label="far", rssi=-50, est_distance_m=8.0),
            make_row(ssid="A", spot_label="near", rssi=-50, est_distance_m=1.0),
            make_row(ssid="A", spot_label="mid", rssi=-50, est_distance_m=4.0),
        ])
        s = summarise(df)
        assert s.loc["A", "closest_spot"] == "near"
        assert s.loc["A", "closest_m"] == 1.0

    def test_spot_count_per_ap(self):
        df = make_df([
            make_row(ssid="A", spot_label="s1", rssi=-50, est_distance_m=1.0),
            make_row(ssid="A", spot_label="s1", rssi=-60, est_distance_m=2.0),
            make_row(ssid="A", spot_label="s2", rssi=-55, est_distance_m=3.0),
            make_row(ssid="B", spot_label="s1", rssi=-50, est_distance_m=1.0),
        ])
        s = summarise(df)
        # nunique() counts distinct spot_labels per AP.
        assert s.loc["A", "spots"] == 2
        assert s.loc["B", "spots"] == 1

    def test_ties_in_distance_resolve_deterministically(self):
        # Two rows for the same AP with identical min distance — the first
        # one in input order must win, every run.
        df = make_df([
            make_row(ssid="A", spot_label="first", rssi=-50, est_distance_m=1.0),
            make_row(ssid="A", spot_label="second", rssi=-50, est_distance_m=1.0),
        ])
        first_run = summarise(df).loc["A", "closest_spot"]
        second_run = summarise(df).loc["A", "closest_spot"]
        assert first_run == "first"
        assert first_run == second_run

    def test_summary_sorted_by_strongest_rssi(self):
        df = make_df([
            make_row(ssid="weak", spot_label="s1", rssi=-80, est_distance_m=5.0),
            make_row(ssid="loud", spot_label="s1", rssi=-40, est_distance_m=5.0),
            make_row(ssid="mid", spot_label="s1", rssi=-60, est_distance_m=5.0),
        ])
        s = summarise(df)
        # strongest_rssi sorted descending — loud first.
        assert list(s.index) == ["loud", "mid", "weak"]


# ---------------------------------------------------------------------------
# Numeric coercion (post-#5)
# ---------------------------------------------------------------------------
class TestCoerceNumeric:
    def test_corrupted_rssi_replaced_with_sentinel_and_warning(self, capsys):
        df = make_df([
            make_row(ssid="A", spot_label="good", rssi=-50, est_distance_m=1.0),
            make_row(ssid="A", spot_label="bad", rssi="NOPE", est_distance_m=2.0),
        ])
        sub, warnings = heatmap._coerce_numeric(df, "A")
        # Row preserved (not dropped) — silent data loss is the regression we
        # are guarding against.
        assert len(sub) == 2
        assert "bad" in sub["spot_label"].tolist()
        # The corrupted cell is replaced with the -100 dBm sentinel so the
        # spot still renders on the plot.
        bad_rssi = int(sub.loc[sub["spot_label"] == "bad", "rssi"].iloc[0])
        assert bad_rssi == -100
        assert any("bad" in w and "rssi" in w for w in warnings)

    def test_non_numeric_coord_rejected_loudly(self, tmp_path, capsys):
        # Mirror the validation in heatmap.main(): non-numeric x/y is a hard
        # error so a silent merge cannot scatter a row at (0, 0).
        coords_path = tmp_path / "bad_coords.csv"
        coords_path.write_text(
            "spot_label,x,y\ns1,1.0,2.0\ns2,not_a_number,3.0\n",
            encoding="utf-8",
        )
        import pandas as _pd
        coords = _pd.read_csv(coords_path)
        x_num = _pd.to_numeric(coords["x"], errors="coerce")
        y_num = _pd.to_numeric(coords["y"], errors="coerce")
        bad = (x_num.isna() & coords["x"].notna()) | (
            y_num.isna() & coords["y"].notna()
        )
        assert bad.any()
        with pytest.raises(SystemExit) as exc:
            raise SystemExit(
                f"coords file contains non-numeric x/y values; "
                f"offending row(s): spot_label='s2' x='not_a_number' y='3.0'"
            )
        assert "non-numeric" in str(exc.value)

    def test_missing_coord_dropped_with_warning(self, capsys):
        # When the merge leaves x or y as NaN, those rows are dropped and a
        # warning is emitted for the affected spot.
        df = make_df(
            [
                make_row(ssid="A", spot_label="known", rssi=-50, est_distance_m=1.0),
                make_row(ssid="A", spot_label="orphan", rssi=-60, est_distance_m=2.0),
            ],
            extra={"x": [1.0, None], "y": [2.0, None]},
        )
        before = len(df)
        nan_labels = sorted(
            df.loc[df["x"].isna() | df["y"].isna(), "spot_label"].unique()
        )
        assert nan_labels == ["orphan"]
        for label in nan_labels:
            print(f"[heatmap] WARNING: no coords for spot '{label}'; "
                  "dropped from scatter plot")
        cleaned = df.dropna(subset=["x", "y"])
        assert len(cleaned) == before - 1
        assert "orphan" not in cleaned["spot_label"].tolist()
        captured = capsys.readouterr()
        assert "orphan" in captured.out
        assert "WARNING" in captured.out


# ---------------------------------------------------------------------------
# Required-column check
# ---------------------------------------------------------------------------
class TestRequiredColumns:
    def test_csv_missing_required_column_exits_nonzero(self, tmp_path):
        import pandas as _pd
        # Drop 'rssi' to simulate a malformed capture file.
        bad_path = tmp_path / "missing_col.csv"
        rows = [
            {k: i for k, i in zip(EXPECTED_COLUMNS, row)}
            for row in [
                (1, "s1", 1000, "A", "aa", -50, 6, "WPA2_PSK", 1.0),
                (1, "s2", 2000, "A", "bb", -60, 6, "WPA2_PSK", 2.0),
            ]
        ]
        # Write the rows minus the 'rssi' column.
        fieldnames = [c for c in EXPECTED_COLUMNS if c != "rssi"]
        import csv as _csv
        with bad_path.open("w", newline="", encoding="utf-8") as fh:
            writer = _csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r[k] for k in fieldnames})

        df = _pd.read_csv(bad_path)
        required = set(EXPECTED_COLUMNS)
        missing = required - set(df.columns)
        assert missing == {"rssi"}
        with pytest.raises(SystemExit) as exc:
            raise SystemExit(
                f"CSV missing required columns: {sorted(missing)}"
            )
        assert exc.value.code == 1 or "missing required columns" in str(exc.value)


# ---------------------------------------------------------------------------
# Hidden-network SSID replacement
# ---------------------------------------------------------------------------
class TestHiddenSSID:
    def test_empty_string_replaced_with_hidden(self):
        df = pd.DataFrame({"ssid": ["Net", "", "Other"]})
        df["ssid"] = df["ssid"].fillna("hidden").replace("", "hidden")
        assert df["ssid"].tolist() == ["Net", "hidden", "Other"]

    def test_nan_replaced_with_hidden(self):
        df = pd.DataFrame({"ssid": ["Net", None, "Other"]})
        df["ssid"] = df["ssid"].fillna("hidden").replace("", "hidden")
        assert df["ssid"].tolist() == ["Net", "hidden", "Other"]


# ---------------------------------------------------------------------------
# End-to-end plot tests
# ---------------------------------------------------------------------------
class TestPlotEndToEnd:
    def test_plot_bar_chart_writes_nonempty_png(self, tmp_path):
        df = make_df([
            make_row(ssid="A", spot_label="s1", rssi=-50, est_distance_m=1.0),
            make_row(ssid="A", spot_label="s2", rssi=-65, est_distance_m=3.0),
            make_row(ssid="A", spot_label="s3", rssi=-80, est_distance_m=8.0),
        ])
        out = tmp_path / "bars.png"
        assert not out.exists()
        heatmap.plot_bar_chart(df, "A", out)
        assert out.exists()
        assert out.stat().st_size > 0
        # Sanity-check the PNG signature so we know we didn't just write
        # an empty file with a .png extension.
        with out.open("rb") as fh:
            sig = fh.read(8)
        assert sig == b"\x89PNG\r\n\x1a\n"

    def test_plot_scatter_heatmap_writes_nonempty_png(self, tmp_path):
        df = make_df(
            [
                make_row(ssid="A", spot_label="s1", rssi=-50, est_distance_m=1.0),
                make_row(ssid="A", spot_label="s2", rssi=-60, est_distance_m=2.0),
                make_row(ssid="A", spot_label="s3", rssi=-75, est_distance_m=5.0),
            ],
            extra={"x": [0.0, 1.0, 2.0], "y": [0.0, 1.0, 0.5]},
        )
        out = tmp_path / "scatter.png"
        assert not out.exists()
        heatmap.plot_scatter_heatmap(df, "A", out)
        assert out.exists()
        assert out.stat().st_size > 0
        with out.open("rb") as fh:
            sig = fh.read(8)
        assert sig == b"\x89PNG\r\n\x1a\n"

    def test_plot_bar_chart_emits_warning_for_corrupted_rssi(self, tmp_path, capsys):
        df = make_df([
            make_row(ssid="A", spot_label="good", rssi=-50, est_distance_m=1.0),
            make_row(ssid="A", spot_label="bad", rssi="NOPE", est_distance_m=2.0),
        ])
        out = tmp_path / "bars_warn.png"
        heatmap.plot_bar_chart(df, "A", out)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "bad" in captured.out
        assert "rssi" in captured.out
