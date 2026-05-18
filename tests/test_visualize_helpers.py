"""Unit tests for visualize.py helpers."""

from __future__ import annotations

from sq_mcp.tools.visualize import _ascii_histogram, _ascii_sparkline, _block_for_fraction

# ---- _block_for_fraction -------------------------------------------------


def test_block_for_fraction_endpoints() -> None:
    assert _block_for_fraction(0.0) == " "
    assert _block_for_fraction(1.0) == "█"


def test_block_for_fraction_negative_returns_space() -> None:
    assert _block_for_fraction(-0.5) == " "


def test_block_for_fraction_above_one_returns_full() -> None:
    assert _block_for_fraction(2.0) == "█"


def test_block_for_fraction_mid_range() -> None:
    # 0.5 should land near the middle of the block ladder
    out = _block_for_fraction(0.5)
    assert out in ("▄", "▅", "▃")


# ---- _ascii_sparkline ----------------------------------------------------


def test_ascii_sparkline_empty() -> None:
    assert _ascii_sparkline([], 60) == ""


def test_ascii_sparkline_constant_series() -> None:
    # All same value → mid-block bar
    out = _ascii_sparkline([100.0, 100.0, 100.0, 100.0], 4)
    assert out == "▄▄▄▄"


def test_ascii_sparkline_monotonic() -> None:
    # Increasing values → low chars on the left, high chars on the right
    out = _ascii_sparkline([1.0, 2.0, 3.0, 4.0, 5.0], 5)
    assert out[0] == " "  # min value → space (frac=0)
    assert out[-1] == "█"  # max value → full block (frac=1)


def test_ascii_sparkline_downsampled() -> None:
    # 100 points into 10 chars
    curve = list(range(100))
    out = _ascii_sparkline([float(x) for x in curve], 10)
    assert len(out) == 10


# ---- _ascii_histogram ----------------------------------------------------


def test_ascii_histogram_no_data() -> None:
    out = _ascii_histogram([], bins=5, width=20)
    assert out["bins"] == []
    assert "no data" in out["ascii"]


def test_ascii_histogram_single_unique_value() -> None:
    out = _ascii_histogram([7.0, 7.0, 7.0], bins=5, width=10)
    # All values identical → degenerate single-bin histogram
    assert len(out["bins"]) == 1
    assert out["bins"][0]["count"] == 3


def test_ascii_histogram_uniform_distribution() -> None:
    # 100 values evenly spread → 10 bins each get ~10 hits
    vals = [float(x) for x in range(100)]
    out = _ascii_histogram(vals, bins=10, width=40)
    assert len(out["bins"]) == 10
    counts = [b["count"] for b in out["bins"]]
    # Total should be 100
    assert sum(counts) == 100
    # Spread reasonably evenly (allow rounding)
    assert all(8 <= c <= 12 for c in counts)


def test_ascii_histogram_skipped_none_and_nan() -> None:
    vals = [1.0, None, 2.0, float("nan"), 3.0]
    out = _ascii_histogram(vals, bins=3, width=10)
    assert sum(b["count"] for b in out["bins"]) == 3
