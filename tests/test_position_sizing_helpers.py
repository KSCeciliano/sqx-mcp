"""Unit tests for position_sizing helpers."""

from __future__ import annotations

from sq_mcp.tools.position_sizing import (
    KellyCriterionArgs,
    PositionSizingCalcArgs,
    PositionSizingPyramidArgs,
    PyramidTier,
    RiskOfRuinArgs,
    _kelly_fraction,
    _kelly_outputs,
    _lot_size,
    _pyramid_tiers,
    _risk_of_ruin,
    _round_to_step,
)

# ---- _round_to_step --------------------------------------------------------


def test_round_to_step_exact_multiple() -> None:
    assert _round_to_step(1.0, 0.01) == 1.0


def test_round_to_step_truncates_not_rounds() -> None:
    # Always floors so we never exceed risk budget
    assert _round_to_step(0.039, 0.01) == 0.03


def test_round_to_step_below_step_returns_zero() -> None:
    assert _round_to_step(0.005, 0.01) == 0.0


# ---- _lot_size: crypto -----------------------------------------------------


def test_lot_size_crypto_basic() -> None:
    args = PositionSizingCalcArgs(
        balance=10_000,
        risk_pct=1.0,  # $100 risk
        entry_price=50_000,
        stop_price=49_000,  # $1000 SL distance
        instrument_kind="crypto",
        contract_size=1.0,
        lot_step=0.001,
        min_lot=0.001,
    )
    out = _lot_size(args)
    assert out["ok"] is True
    # risk $100 / $1000 distance = 0.1 BTC
    assert out["raw_lots"] == 0.1
    assert out["final_lots"] == 0.1
    assert out["risk_dollars"] == 100.0


def test_lot_size_crypto_zero_distance_errors() -> None:
    args = PositionSizingCalcArgs(
        balance=10_000,
        risk_pct=1.0,
        entry_price=50_000,
        stop_price=50_000,
        instrument_kind="crypto",
    )
    out = _lot_size(args)
    assert out["ok"] is False
    assert "zero stop-loss" in out["error"]


def test_lot_size_crypto_respects_min_lot() -> None:
    args = PositionSizingCalcArgs(
        balance=100,  # very small account
        risk_pct=0.1,  # $0.10 risk
        entry_price=50_000,
        stop_price=49_000,  # $1000 SL distance → 0.0001 BTC raw
        instrument_kind="crypto",
        contract_size=1.0,
        min_lot=0.001,
        lot_step=0.001,
    )
    out = _lot_size(args)
    assert out["ok"] is True
    # raw < min_lot, but clipped to min_lot
    assert out["final_lots"] == 0.001


def test_lot_size_crypto_respects_max_lot() -> None:
    args = PositionSizingCalcArgs(
        balance=1_000_000_000,
        risk_pct=10.0,
        entry_price=50_000,
        stop_price=49_999,
        instrument_kind="crypto",
        contract_size=1.0,
        max_lot=5.0,
    )
    out = _lot_size(args)
    assert out["final_lots"] == 5.0


# ---- _lot_size: forex ------------------------------------------------------


def test_lot_size_forex_basic() -> None:
    # 100 pips SL, $10/pip/lot, $100 risk → 0.1 lots
    args = PositionSizingCalcArgs(
        balance=10_000,
        risk_pct=1.0,
        entry_price=1.1000,
        stop_price=1.0900,  # 100 pip SL
        instrument_kind="forex",
        pip_size=0.0001,
        pip_value_per_lot=10.0,
        lot_step=0.01,
    )
    out = _lot_size(args)
    assert out["ok"] is True
    assert abs(out["raw_lots"] - 0.1) < 1e-6


# ---- _kelly_fraction -------------------------------------------------------


def test_kelly_no_edge() -> None:
    # 50/50 with 1:1 payoff → f = 0
    f = _kelly_fraction(0.5, 1.0, 1.0)
    assert f == 0.0


def test_kelly_negative_edge_clamped_zero() -> None:
    # 40% win, 1:1 payoff → negative edge, clamped to 0
    f = _kelly_fraction(0.4, 1.0, 1.0)
    assert f == 0.0


def test_kelly_positive_edge() -> None:
    # 60% win, 1:1 payoff → f = 0.6 - 0.4/1 = 0.2
    f = _kelly_fraction(0.6, 1.0, 1.0)
    assert abs(f - 0.2) < 1e-9


def test_kelly_with_payoff_ratio() -> None:
    # 50% win, 2:1 payoff → f = 0.5 - 0.5/2 = 0.25
    f = _kelly_fraction(0.5, 2.0, 1.0)
    assert abs(f - 0.25) < 1e-9


def test_kelly_outputs_negative_edge_warns() -> None:
    out = _kelly_outputs(KellyCriterionArgs(win_rate=0.3, avg_win=1.0, avg_loss=1.0))
    assert out["full_kelly_fraction"] == 0.0
    assert "no edge" in out["interpretation"]


def test_kelly_outputs_extreme_warns() -> None:
    # Very high edge → > 0.5
    out = _kelly_outputs(KellyCriterionArgs(win_rate=0.9, avg_win=5.0, avg_loss=1.0))
    assert out["full_kelly_fraction"] > 0.5
    assert "extreme" in out["interpretation"]


def test_kelly_outputs_quarter_kelly() -> None:
    out = _kelly_outputs(KellyCriterionArgs(win_rate=0.6, avg_win=1.0, avg_loss=1.0, fraction=0.25))
    assert abs(out["quarter_kelly_fraction"] - 0.05) < 1e-9
    assert abs(out["recommended_fraction"] - 0.05) < 1e-9


# ---- _pyramid_tiers --------------------------------------------------------


def test_pyramid_no_tiers_returns_initial_only() -> None:
    out = _pyramid_tiers(
        PositionSizingPyramidArgs(
            initial_entry_price=50_000, initial_size=0.1, tiers=[], stop_price=49_000
        )
    )
    assert out["ok"] is True
    assert len(out["tiers"]) == 1
    assert out["final_cumulative_size"] == 0.1
    assert out["final_avg_entry"] == 50_000


def test_pyramid_avg_entry_weighted_correctly() -> None:
    # Initial: 0.1 @ 50000, add 0.1 @ 52000 → avg = (5000 + 5200) / 0.2 = 51000
    out = _pyramid_tiers(
        PositionSizingPyramidArgs(
            initial_entry_price=50_000,
            initial_size=0.1,
            tiers=[PyramidTier(add_at_price=52_000, add_size=0.1)],
            stop_price=49_000,
        )
    )
    assert out["final_avg_entry"] == 51_000


def test_pyramid_r_multiple_correct() -> None:
    # SL distance = 1000 → +1000 from entry = +1R
    out = _pyramid_tiers(
        PositionSizingPyramidArgs(
            initial_entry_price=50_000,
            initial_size=0.1,
            tiers=[PyramidTier(add_at_price=51_000, add_size=0.05)],
            stop_price=49_000,
        )
    )
    assert out["tiers"][1]["r_multiple"] == 1.0


# ---- _risk_of_ruin ---------------------------------------------------------


def test_risk_of_ruin_no_edge_is_certain() -> None:
    out = _risk_of_ruin(
        RiskOfRuinArgs(win_rate=0.5, payoff_ratio=1.0, risk_per_trade_pct=1.0)
    )
    assert out["risk_of_ruin"] == 1.0
    assert "certain" in out["note"]


def test_risk_of_ruin_high_edge_is_low() -> None:
    out = _risk_of_ruin(
        RiskOfRuinArgs(
            win_rate=0.6, payoff_ratio=2.0, risk_per_trade_pct=1.0, ruin_threshold_pct=50.0
        )
    )
    assert out["edge"] > 0
    assert out["risk_of_ruin"] < 0.01


def test_risk_of_ruin_marginal_edge_moderate_to_high() -> None:
    out = _risk_of_ruin(
        RiskOfRuinArgs(
            win_rate=0.52, payoff_ratio=1.0, risk_per_trade_pct=5.0, ruin_threshold_pct=50.0
        )
    )
    assert out["edge"] > 0
    assert "interpretation" in out
