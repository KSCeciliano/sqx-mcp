"""Unit tests for reports.py CSV / Markdown helpers."""

from __future__ import annotations

from sq_mcp.tools.audit import Finding
from sq_mcp.tools.reports import _CSV_COLUMNS, _row_for_csv

# ---- _row_for_csv ---------------------------------------------------------


def test_row_for_csv_keeps_known_columns_only() -> None:
    r = {
        "rel": "strat.sqx",
        "trades": 100,
        "net_profit": 500.0,
        "weird_extra_column": "ignored",
        "fitness_oos": 0.5,
    }
    out = _row_for_csv(r)
    assert set(out.keys()) == set(_CSV_COLUMNS)
    assert out["rel"] == "strat.sqx"
    assert out["trades"] == 100
    assert out["fitness_oos"] == 0.5
    # Missing metrics in the input become None
    assert out["drawdown_pct"] is None


def test_row_for_csv_empty_input() -> None:
    out = _row_for_csv({})
    assert set(out.keys()) == set(_CSV_COLUMNS)
    assert all(v is None for v in out.values())


def test_csv_columns_includes_essentials() -> None:
    # Sanity check on the contract
    for col in (
        "rel", "strategy_name", "trades", "fitness_oos", "drawdown_pct",
        "trades_hash", "fingerprint_exact",
    ):
        assert col in _CSV_COLUMNS


# Smoke test of the markdown helper — uses a Finding dataclass + a minimal
# "info" stub since we can't trivially parse a fake .sqx here.


class _StubMeta:
    equity_curve_full = [100.0, 110.0, 120.0, 130.0]
    equity_curve_is: list[float] = []
    equity_curve_oos: list[float] = []


class _StubInfo:
    meta = _StubMeta()


def test_markdown_for_one_strategy_includes_verdict_and_findings() -> None:
    from sq_mcp.tools.reports import _markdown_for_one_strategy

    metrics = {
        "strategy_name": "demo-strat",
        "symbol": "BTCUSDT",
        "timeframe": "H1",
        "trades": 100,
        "net_profit": 500.0,
        "fitness_is": 0.5,
        "fitness_oos": 0.5,
    }
    findings = [
        Finding(
            code="OVERFIT_OOS_DEGRADATION",
            severity="critical",
            title="X",
            message="overfit",
            suggestion="retest",
        )
    ]
    out = _markdown_for_one_strategy(
        info=_StubInfo(), metrics=metrics, findings=findings
    )
    assert "demo-strat" in out
    # Equity section labels appear
    assert "max_drawdown_pct_of_peak" in out
    # When verdict isn't green, findings section is present
    assert "OVERFIT_OOS_DEGRADATION" in out
