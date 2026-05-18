"""Unit tests for mt5_reports parsing helpers."""

from __future__ import annotations

from sq_mcp.tools.mt5_reports import (
    _coerce_metric,
    _diff_with_sqx,
    _extract_metric_value,
    _parse_mt5_report,
    _strip_html,
)

# ---- _strip_html ---------------------------------------------------------


def test_strip_html_removes_tags_collapses_whitespace() -> None:
    html = "<html><body>Profit Factor:    <b>1.45</b></body></html>"
    out = _strip_html(html)
    assert "Profit Factor: 1.45" in out


def test_strip_html_handles_nested_tags() -> None:
    html = "<table><tr><td>Total Trades</td><td>250</td></tr></table>"
    out = _strip_html(html)
    assert "Total Trades" in out
    assert "250" in out


# ---- _coerce_metric ------------------------------------------------------


def test_coerce_metric_extracts_first_numeric() -> None:
    assert _coerce_metric("1234.56 USD") == 1234.56
    assert _coerce_metric("12.50 (4.5%)") == 12.5
    assert _coerce_metric("-500.00") == -500.0


def test_coerce_metric_handles_none_and_empty() -> None:
    assert _coerce_metric(None) is None
    assert _coerce_metric("") is None
    assert _coerce_metric("no number here") is None


def test_coerce_metric_european_comma_fallback() -> None:
    # "1,5" should parse as 1.5 if the dot path fails
    assert _coerce_metric("1,5") == 1.5


# ---- _extract_metric_value ----------------------------------------------


def test_extract_metric_value_basic() -> None:
    text = "Profit Factor: 1.45 Recovery Factor: 2.3 "
    out = _extract_metric_value(text, "Profit Factor")
    assert out is not None
    assert "1.45" in out


def test_extract_metric_value_missing_label() -> None:
    text = "Net Profit: 100"
    assert _extract_metric_value(text, "Profit Factor") is None


# ---- _parse_mt5_report ---------------------------------------------------


def test_parse_mt5_report_extracts_known_metrics() -> None:
    html = b"""<html><body>
    <table>
    <tr><td>Total Net Profit</td><td>1500.50</td></tr>
    <tr><td>Profit Factor</td><td>1.45</td></tr>
    <tr><td>Total Trades</td><td>250</td></tr>
    <tr><td>Sharpe Ratio</td><td>0.85</td></tr>
    </table>
    </body></html>"""
    out = _parse_mt5_report(html)
    assert (out["total_net_profit"] or {}).get("value") == 1500.50
    assert (out["profit_factor"] or {}).get("value") == 1.45
    assert (out["total_trades"] or {}).get("value") == 250
    assert (out["sharpe_ratio"] or {}).get("value") == 0.85


def test_parse_mt5_report_missing_metrics_return_none_values() -> None:
    html = b"<html>nothing useful here</html>"
    out = _parse_mt5_report(html)
    # Every key present with value=None
    for k in (
        "total_net_profit",
        "profit_factor",
        "total_trades",
        "balance_drawdown_pct",
    ):
        assert (out[k] or {}).get("value") is None


def test_parse_mt5_report_handles_alternative_label() -> None:
    # 'Net Profit' (no 'Total') should still match
    html = b"<html><body>Net Profit: 999.99</body></html>"
    out = _parse_mt5_report(html)
    assert (out["total_net_profit"] or {}).get("value") == 999.99


# ---- _diff_with_sqx -----------------------------------------------------


def test_diff_with_sqx_match_when_values_close() -> None:
    mt5 = {
        "total_net_profit": {"value": 1000.0},
        "total_trades": {"value": 100},
        "balance_drawdown_pct": {"value": 10.0},
    }
    sqx = {
        "net_profit": 1050.0,  # 5% diff
        "trades": 102,  # 2% diff
        "drawdown_pct": 10.5,  # 5% diff
    }
    out = _diff_with_sqx(mt5, sqx, warn_threshold=0.10)
    # No warn within 10% threshold
    assert out["warn_count"] == 0


def test_diff_with_sqx_warns_on_big_disagreement() -> None:
    mt5 = {"total_net_profit": {"value": 1000.0}}
    sqx = {"net_profit": 3000.0}  # 200% diff
    out = _diff_with_sqx(mt5, sqx, warn_threshold=0.10)
    assert out["warn_count"] == 1
    profit_diff = next(d for d in out["diffs"] if d["mt5_key"] == "total_net_profit")
    assert profit_diff["status"] == "warn"


def test_diff_with_sqx_marks_missing() -> None:
    mt5 = {"total_net_profit": {"value": None}}
    sqx = {"net_profit": 500.0}
    out = _diff_with_sqx(mt5, sqx, warn_threshold=0.10)
    profit_diff = next(d for d in out["diffs"] if d["mt5_key"] == "total_net_profit")
    assert profit_diff["status"] == "missing"


def test_diff_with_sqx_zero_sqx_value_treated_safely() -> None:
    mt5 = {"total_net_profit": {"value": 100.0}}
    sqx = {"net_profit": 0.0}  # Division would otherwise fail
    out = _diff_with_sqx(mt5, sqx, warn_threshold=0.10)
    profit_diff = next(d for d in out["diffs"] if d["mt5_key"] == "total_net_profit")
    # MT5 says 100, SQ says 0 → warn (clear disagreement)
    assert profit_diff["status"] == "warn"
    assert profit_diff["relative_diff"] is None
