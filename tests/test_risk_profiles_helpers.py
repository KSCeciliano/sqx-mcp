"""Unit tests for risk_profiles helpers."""

from __future__ import annotations

from sq_mcp.tools.risk_profiles import PROFILES, _compare_against, _profile_summary

# ---- PROFILES sanity -------------------------------------------------------


def test_all_profiles_have_required_keys() -> None:
    required = {
        "description",
        "risk_per_trade_pct",
        "max_drawdown_threshold_pct",
        "max_concurrent_positions",
        "compounding",
        "recommended_kelly_fraction",
        "mm_method_type",
        "mm_params",
        "min_oos_is_ratio",
        "use_case",
    }
    for name, prof in PROFILES.items():
        missing = required - set(prof.keys())
        assert not missing, f"{name} missing keys: {missing}"


def test_profiles_ordered_increasing_risk() -> None:
    # Sanity: risk per trade should ascend from ultra_conservative → aggressive
    seq = ["ultra_conservative", "conservative", "moderate", "aggressive"]
    risks = [PROFILES[s]["risk_per_trade_pct"] for s in seq]
    assert risks == sorted(risks)
    dds = [PROFILES[s]["max_drawdown_threshold_pct"] for s in seq]
    assert dds == sorted(dds)


def test_profiles_concurrent_positions_ascending() -> None:
    seq = ["ultra_conservative", "conservative", "moderate", "aggressive"]
    concs = [PROFILES[s]["max_concurrent_positions"] for s in seq]
    assert concs == sorted(concs)


def test_profile_summary_includes_name() -> None:
    out = _profile_summary("moderate", PROFILES["moderate"])
    assert out["name"] == "moderate"
    assert out["risk_per_trade_pct"] == 1.0


# ---- _compare_against ------------------------------------------------------


def test_compare_passes_under_cap() -> None:
    out = _compare_against(
        "moderate",
        PROFILES["moderate"],
        strategy_dd=5.0,  # under 10% cap
        oos_is_ratio=0.7,  # above 0.4 min
        trades=500,
    )
    assert out["verdict"] == "fits"
    assert out["violations"] == []


def test_compare_dd_too_high() -> None:
    out = _compare_against(
        "conservative",
        PROFILES["conservative"],
        strategy_dd=15.0,  # exceeds 5% cap
        oos_is_ratio=0.7,
        trades=500,
    )
    assert out["verdict"] == "violates"
    assert any("max_drawdown_threshold_pct" in v for v in out["violations"])


def test_compare_oos_ratio_too_low() -> None:
    out = _compare_against(
        "moderate",
        PROFILES["moderate"],
        strategy_dd=5.0,
        oos_is_ratio=0.1,  # well below 0.4
        trades=500,
    )
    assert out["verdict"] == "violates"
    assert any("min_oos_is_ratio" in v for v in out["violations"])


def test_compare_too_few_trades_warns() -> None:
    out = _compare_against(
        "moderate",
        PROFILES["moderate"],
        strategy_dd=5.0,
        oos_is_ratio=0.7,
        trades=10,
    )
    assert out["verdict"] == "violates"
    assert any("100" in v and "trades" in v for v in out["violations"])


def test_compare_skips_oos_check_if_none() -> None:
    out = _compare_against(
        "moderate",
        PROFILES["moderate"],
        strategy_dd=5.0,
        oos_is_ratio=None,
        trades=500,
    )
    assert out["verdict"] == "fits"


def test_compare_returns_profile_caps_and_inputs() -> None:
    out = _compare_against(
        "aggressive",
        PROFILES["aggressive"],
        strategy_dd=5.0,
        oos_is_ratio=0.5,
        trades=200,
    )
    assert "profile_caps" in out
    assert out["profile_caps"]["risk_per_trade_pct"] == 2.0
    assert out["strategy_inputs"]["drawdown_pct"] == 5.0
