"""Unit tests for portfolio_audit._audit_portfolio."""

from __future__ import annotations

from sq_mcp.tools.portfolio_audit import _audit_portfolio


def _make_row(**kwargs) -> dict:
    base = {
        "rel": "x.sqx",
        "trades": 100,
        "net_profit": 500.0,
        "fitness_oos": 0.5,
        "fitness_is": 0.6,
        "drawdown_pct": 10.0,
        "profit_to_dd_ratio": 5.0,
        "return_pct": 50.0,
        "oos_is_ratio": 0.83,
        "history_years": 3.0,
        "trades_hash": "H1",
        "fingerprint_exact": "FP1",
        "symbol": "BTCUSDT",
        "timeframe": "H1",
    }
    base.update(kwargs)
    return base


def test_empty_databank_flagged() -> None:
    findings = _audit_portfolio([])
    assert len(findings) == 1
    assert findings[0].code == "EMPTY_DATABANK"


def test_low_profitable_rate_flagged() -> None:
    rows = [
        _make_row(rel=f"s{i}.sqx", net_profit=-100.0, trades_hash=f"H{i}")
        for i in range(8)
    ]
    rows.append(_make_row(rel="winner.sqx", net_profit=500.0, trades_hash="HX"))
    findings = _audit_portfolio(rows)
    codes = [f.code for f in findings]
    assert "LOW_PROFITABLE_RATE" in codes


def test_systemic_overfit_flagged_when_most_have_low_ratio() -> None:
    rows = [
        _make_row(rel=f"s{i}.sqx", oos_is_ratio=0.2, trades_hash=f"H{i}")
        for i in range(8)
    ]
    rows.append(_make_row(rel="ok.sqx", oos_is_ratio=0.9, trades_hash="HX"))
    findings = _audit_portfolio(rows)
    codes = [f.code for f in findings]
    assert "SYSTEMIC_OVERFIT" in codes


def test_high_duplicate_rate_flagged() -> None:
    rows = [_make_row(rel=f"s{i}.sqx", trades_hash="SAME") for i in range(10)]
    rows.append(_make_row(rel="unique.sqx", trades_hash="OTHER"))
    findings = _audit_portfolio(rows)
    codes = [f.code for f in findings]
    assert "HIGH_DUPLICATE_RATE" in codes


def test_high_concentration_flagged() -> None:
    # Heavily concentrated: 18 in H1, 2 in H2 → HHI ~0.82, normalized ~0.64
    rows = [_make_row(rel=f"a{i}.sqx", trades_hash="H1") for i in range(18)]
    rows += [_make_row(rel=f"b{i}.sqx", trades_hash="H2") for i in range(2)]
    findings = _audit_portfolio(rows)
    codes = [f.code for f in findings]
    assert "HIGH_TRADES_HASH_CONCENTRATION" in codes


def test_single_symbol_flagged_when_multi_strategy() -> None:
    rows = [
        _make_row(rel=f"s{i}.sqx", symbol="BTCUSDT", trades_hash=f"H{i}")
        for i in range(20)
    ]
    findings = _audit_portfolio(rows)
    codes = [f.code for f in findings]
    assert "SINGLE_SYMBOL_ONLY" in codes


def test_single_timeframe_flagged() -> None:
    rows = [
        _make_row(
            rel=f"s{i}.sqx", symbol=f"SYM{i%3}", timeframe="H1", trades_hash=f"H{i}"
        )
        for i in range(15)
    ]
    findings = _audit_portfolio(rows)
    codes = [f.code for f in findings]
    assert "SINGLE_TIMEFRAME_ONLY" in codes


def test_tiny_databank_flagged() -> None:
    rows = [_make_row(rel=f"s{i}.sqx", trades_hash=f"H{i}") for i in range(5)]
    findings = _audit_portfolio(rows)
    codes = [f.code for f in findings]
    assert "TINY_DATABANK" in codes


def test_clean_portfolio_no_critical_or_high() -> None:
    """A healthy portfolio shouldn't fire critical / high portfolio findings."""
    rows = []
    for i in range(50):
        rows.append(_make_row(
            rel=f"s{i}.sqx",
            symbol=f"SYM{i % 3}",
            timeframe=f"TF{i % 2}",
            trades_hash=f"H{i}",
            oos_is_ratio=0.85,
            net_profit=500.0,
        ))
    findings = _audit_portfolio(rows)
    # Allow info or low severity, but no high/critical
    severities = {f.severity for f in findings}
    assert "critical" not in severities
    assert "high" not in severities
