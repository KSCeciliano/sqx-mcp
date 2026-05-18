"""Unit tests for audit heuristics."""

from __future__ import annotations

from sq_mcp.tools.audit import (
    _audit_strategy_metrics,
    _severity_rank,
)


def _findings_by_code(findings) -> dict[str, str]:
    return {f.code: f.severity for f in findings}


# ---- strategy heuristics ----------------------------------------------------


def test_audit_passes_clean_strategy() -> None:
    m = {
        "trades": 250,
        "fitness_is": 0.85,
        "fitness_oos": 0.78,
        "oos_is_ratio": 0.92,
        "profit_to_dd_ratio": 4.5,
        "drawdown_pct": 8.0,
        "ambiguous_trades": 0,
        "strategy_problems": 0,
        "history_years": 5.0,
        "drawdown_abs": 850.0,
        "trades_per_year": 50,
    }
    findings = _audit_strategy_metrics(m)
    assert findings == []


def test_audit_flags_too_few_trades() -> None:
    m = {"trades": 15}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("TOO_FEW_TRADES") == "high"


def test_audit_flags_no_oos_when_oos_zero() -> None:
    m = {"fitness_is": 0.5, "fitness_oos": 0.0, "oos_is_ratio": 0.0}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("NO_OOS_FITNESS") == "high"
    # And specifically NOT OVERFIT_OOS_DEGRADATION when OOS is 0 (would double-flag)
    assert "OVERFIT_OOS_DEGRADATION" not in by_code


def test_audit_flags_overfit_when_oos_positive_but_low() -> None:
    m = {"fitness_is": 0.9, "fitness_oos": 0.3, "oos_is_ratio": 0.33}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("OVERFIT_OOS_DEGRADATION") == "critical"


def test_audit_flags_unrealistic_profit_to_dd() -> None:
    m = {"profit_to_dd_ratio": 50.0}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("UNREALISTIC_PROFIT_TO_DD") == "high"


def test_audit_flags_drawdown_over_half() -> None:
    m = {"drawdown_pct": 65.0}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("DRAWDOWN_OVER_HALF") == "high"


def test_audit_flags_ambiguous_trades() -> None:
    m = {"ambiguous_trades": 3}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("AMBIGUOUS_TRADES") == "medium"


def test_audit_flags_engine_problems() -> None:
    m = {"strategy_problems": 1}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("STRATEGY_PROBLEMS") == "high"


def test_audit_flags_short_backtest() -> None:
    m = {"history_years": 0.4}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("VERY_SHORT_BACKTEST") == "medium"


def test_audit_flags_suspiciously_low_dd() -> None:
    m = {"drawdown_abs": 0.005}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("SUSPICIOUSLY_LOW_DRAWDOWN") == "medium"


def test_audit_flags_extreme_trade_frequency() -> None:
    m = {"trades_per_year": 8000}
    by_code = _findings_by_code(_audit_strategy_metrics(m))
    assert by_code.get("EXTREMELY_HIGH_TRADE_FREQ") == "medium"


def test_audit_combines_multiple_findings() -> None:
    m = {
        "trades": 10,
        "fitness_is": 0.9,
        "fitness_oos": 0.1,
        "oos_is_ratio": 0.11,
        "profit_to_dd_ratio": 30.0,
        "drawdown_pct": 70.0,
    }
    findings = _audit_strategy_metrics(m)
    codes = {f.code for f in findings}
    assert codes >= {
        "TOO_FEW_TRADES",
        "OVERFIT_OOS_DEGRADATION",
        "UNREALISTIC_PROFIT_TO_DD",
        "DRAWDOWN_OVER_HALF",
    }


def test_severity_rank_ordering() -> None:
    assert _severity_rank("critical") < _severity_rank("high")
    assert _severity_rank("high") < _severity_rank("medium")
    assert _severity_rank("medium") < _severity_rank("low")
    assert _severity_rank("low") < _severity_rank("info")
    assert _severity_rank("unknown") > _severity_rank("info")
