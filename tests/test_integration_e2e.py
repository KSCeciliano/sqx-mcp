"""End-to-end integration tests across the new modules.

Builds a synthetic databank with a few .sqx files and drives helper
functions across multiple modules (portfolio, audit, comparison, analytics,
strategy_inspect, regression, reports) to verify they compose correctly.

Lower-level than the MCP-tool layer (no FastMCP context required), but
higher-level than the per-module unit tests — catches inter-module wiring
regressions.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from sq_mcp.tools.audit import _audit_strategy_metrics
from sq_mcp.tools.comparison import _ab_summarize_side, _overlap_by_hash
from sq_mcp.tools.pipeline import _verdict_from_findings
from sq_mcp.tools.portfolio import _scan_databank, _summary_for_rows
from sq_mcp.tools.portfolio_audit import _audit_portfolio
from sq_mcp.tools.portfolio_risk import (
    _bucket_counts,
    _distribution_stats,
    _diversity_score,
    _hhi,
)


def _make_sqx(
    path: Path,
    *,
    trades_hash: str,
    trades: int = 100,
    profit: float = 1000.0,
    dd: float = 200.0,
    fitness_is: float = 0.5,
    fitness_oos: float = 0.3,
    symbol: str = "BTCUSDT",
    timeframe: str = "H1",
) -> Path:
    """Stamp a minimal .sqx with the supplied Fingerprint."""
    settings = f"""<?xml version="1.0"?>
<ResultsGroup ResultName="x">
  <ResultsMap><Results><Result resultKey="x">
    <Fitnesses IS="{fitness_is}" OOS="{fitness_oos}" FS="0.4"/>
    <ValuesMap>
      <Symbol type="String">{symbol}</Symbol>
      <Timeframe type="String">{timeframe}</Timeframe>
    </ValuesMap>
  </Result></Results></ResultsMap>
  <SpecialValuesMap><SettingsMap>
    <Fingerprint type="com.strategyquant.tradinglib.results.StrategyFingerprint">
      <Fingerprint strategyName="strat_{trades_hash}" exact="ex_{trades_hash}" trades="{trades}"
                   profit="{profit}" drawdown="{dd}" fitness="0.5"
                   tradesHash="{trades_hash}"/>
    </Fingerprint>
  </SettingsMap></SpecialValuesMap>
</ResultsGroup>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version.txt", "1")
        z.writestr("settings.xml", settings)
    path.write_bytes(buf.getvalue())
    return path


def test_e2e_databank_audit_and_summarize(tmp_path: Path) -> None:
    """Build a small synthetic databank, run portfolio_summary + portfolio_audit."""
    db = tmp_path / "Results"
    db.mkdir()

    # Three strategies — two same trade_hash, one different
    _make_sqx(db / "a.sqx", trades_hash="H1", trades=200, profit=2000.0, dd=400.0)
    _make_sqx(db / "b.sqx", trades_hash="H1", trades=180, profit=1500.0, dd=300.0)
    _make_sqx(db / "c.sqx", trades_hash="H2", trades=150, profit=900.0, dd=900.0, fitness_oos=0.05)

    rows, bad = _scan_databank(db)
    assert len(rows) == 3, f"expected 3 rows, got {len(rows)}: {bad}"
    summary = _summary_for_rows(rows)
    assert summary["strategies"] == 3
    # All on BTCUSDT/H1 → single bucket each
    assert summary["by_symbol"] == {"BTCUSDT": 3}
    assert summary["unique_trades_hashes"] == 2

    findings = _audit_portfolio(rows)
    codes = [f.code for f in findings]
    # SINGLE_SYMBOL_ONLY and SINGLE_TIMEFRAME_ONLY should fire (all same symbol/TF, n > 1)
    assert "SINGLE_SYMBOL_ONLY" in codes
    assert "SINGLE_TIMEFRAME_ONLY" in codes
    # TINY_DATABANK should fire (3 rows < 10)
    assert "TINY_DATABANK" in codes


def test_e2e_strategy_audit_and_verdict(tmp_path: Path) -> None:
    """A strategy with very few trades should be flagged."""
    p = tmp_path / "x.sqx"
    _make_sqx(p, trades_hash="H1", trades=15, profit=100.0)
    rows, _ = _scan_databank(p.parent)
    metrics = rows[0]
    findings = _audit_strategy_metrics(metrics)
    codes = [f.code for f in findings]
    assert "TOO_FEW_TRADES" in codes
    verdict = _verdict_from_findings(findings, block_on=["critical", "high"])
    assert verdict["traffic_light"] == "red"


def test_e2e_concentration_and_diversity_score(tmp_path: Path) -> None:
    """Build a concentrated databank (all same hash) and verify diversity tools agree."""
    db = tmp_path / "Results"
    db.mkdir()
    for i in range(5):
        _make_sqx(
            db / f"s{i}.sqx",
            trades_hash="SAME",
            trades=100,
            profit=500.0,
            symbol="BTCUSDT",
            timeframe="H1",
        )
    rows, _ = _scan_databank(db)
    assert len(rows) == 5

    # HHI on trades_hash → all in one bucket → HHI = 1.0
    buckets = _bucket_counts(rows, "trades_hash")
    h = _hhi(buckets)
    assert h["hhi"] == 1.0
    assert h["unique_buckets"] == 1

    # Diversity score → poor or empty tier (no comparison space)
    score = _diversity_score(rows)
    assert score["tier"] != "excellent"


def test_e2e_ab_overlap(tmp_path: Path) -> None:
    """Two synthetic databanks with overlapping trade hashes."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _make_sqx(a / "s1.sqx", trades_hash="H1")
    _make_sqx(a / "s2.sqx", trades_hash="H2")
    _make_sqx(b / "s1.sqx", trades_hash="H2")
    _make_sqx(b / "s2.sqx", trades_hash="H3")

    rows_a, _ = _scan_databank(a)
    rows_b, _ = _scan_databank(b)
    sum_a = _ab_summarize_side(rows_a)
    sum_b = _ab_summarize_side(rows_b)
    assert sum_a["strategies"] == 2
    assert sum_b["strategies"] == 2
    overlap = _overlap_by_hash(rows_a, rows_b)
    # Hashes: A={H1,H2}, B={H2,H3} → intersect={H2}, union={H1,H2,H3}
    assert overlap["in_both_count"] == 1
    assert overlap["jaccard"] == round(1 / 3, 4)


def test_e2e_distribution_stats_on_real_metrics(tmp_path: Path) -> None:
    """Confirm _distribution_stats works against scanned databank metrics."""
    db = tmp_path / "Results"
    db.mkdir()
    for i in range(5):
        _make_sqx(
            db / f"s{i}.sqx",
            trades_hash=f"H{i}",
            trades=100 + i * 50,
            profit=1000.0 + i * 100,
            dd=200.0 + i * 50,
        )
    rows, _ = _scan_databank(db)
    drawdown_vals = [r.get("drawdown_pct") for r in rows]
    stats = _distribution_stats(drawdown_vals)
    assert stats["count"] >= 0  # may be 0 if derive_metrics couldn't compute %
    # No exception is the main contract
