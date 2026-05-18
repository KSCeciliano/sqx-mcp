"""Triple-barrier labeling and bet sizing from probabilities.

The triple-barrier method (Lopez de Prado, "Advances in Financial Machine
Learning", Ch. 3) labels each entry point as +1 / -1 / 0 based on which of
three barriers it hits first:

- Upper barrier (profit target): label = +1
- Lower barrier (stop loss): label = -1
- Vertical barrier (time horizon expiry): label = 0

This matches how real trades actually exit (TP/SL/time), not how a naive
backtest defines a "win." Combined with bet sizing from probability (a
classifier's confidence in P(win)), it unlocks meta-labeling: train a
classifier on top of an existing primary signal, then bet size = f(P(win)).

Tools:

- ``label_triple_barrier`` — apply the three barriers to a series of entry
  prices and return per-entry label + which barrier hit + the exit index.
- ``label_meta_label_outcomes`` — given binary labels from a primary signal
  and the realized PnL, produce meta-labels (1 if primary was right, 0 if
  primary was wrong) for downstream classifier training.
- ``bet_size_from_probability`` — Lopez de Prado's bet-sizing transform:
  size = signed value of a two-tailed test on the predicted probability.
- ``bet_size_average_active`` — average overlapping bet sizes to produce
  a smoothed target position (avoids whipsaw entries).
"""

from __future__ import annotations

import math
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from sq_mcp.tools._numerics import (
    NumericValidationError,
    validate_finite_floats,
)
from sq_mcp.tools.overfit_diag import _norm_cdf


class TripleBarrierArgs(BaseModel):
    prices: list[float] = Field(..., min_length=2, max_length=500_000)
    entry_indices: list[int] = Field(..., min_length=1, max_length=200_000)
    profit_target_pct: float = Field(..., gt=0.0, le=100.0)
    stop_loss_pct: float = Field(..., gt=0.0, le=100.0)
    time_horizon: int = Field(..., ge=1, le=500_000)
    side: Literal["long", "short"] = "long"


class MetaLabelArgs(BaseModel):
    primary_signal: list[int] = Field(..., min_length=1, max_length=500_000)
    realized_pnl: list[float] = Field(..., min_length=1, max_length=500_000)


class BetSizeProbArgs(BaseModel):
    probability: float = Field(..., gt=0.0, lt=1.0)
    side: Literal["long", "short"] = "long"
    n_classes: int = Field(2, ge=2, le=10)
    step_size: float = Field(0.0, ge=0.0, le=1.0)


class BetSizeAverageArgs(BaseModel):
    bet_sizes: list[float] = Field(..., min_length=1, max_length=500_000)
    active_windows: list[int] = Field(
        ..., min_length=1, max_length=500_000,
        description=(
            "Parallel list: for each bet at index i, how many subsequent "
            "bars (incl. i) this bet remains active. Used to compute the "
            "set of overlapping active bets at each bar."
        ),
    )


def _label_one_entry(
    prices: list[float],
    entry_idx: int,
    pt_pct: float,
    sl_pct: float,
    horizon: int,
    side: str,
) -> dict[str, Any]:
    if entry_idx < 0 or entry_idx >= len(prices):
        return {
            "label": None,
            "barrier": "invalid_entry",
            "exit_index": None,
            "exit_price": None,
            "return_pct": None,
        }
    entry_price = prices[entry_idx]
    if entry_price <= 0:
        return {
            "label": None,
            "barrier": "non_positive_entry",
            "exit_index": None,
            "exit_price": None,
            "return_pct": None,
        }
    sign = 1.0 if side == "long" else -1.0
    end_idx = min(entry_idx + horizon, len(prices) - 1)
    for i in range(entry_idx + 1, end_idx + 1):
        ret = sign * (prices[i] - entry_price) / entry_price * 100.0
        if ret >= pt_pct:
            return {
                "label": 1,
                "barrier": "profit_target",
                "exit_index": i,
                "exit_price": round(prices[i], 8),
                "return_pct": round(ret, 6),
            }
        if ret <= -sl_pct:
            return {
                "label": -1,
                "barrier": "stop_loss",
                "exit_index": i,
                "exit_price": round(prices[i], 8),
                "return_pct": round(ret, 6),
            }
    # Time barrier hit
    final_ret = sign * (prices[end_idx] - entry_price) / entry_price * 100.0
    return {
        "label": 0,
        "barrier": "time_horizon",
        "exit_index": end_idx,
        "exit_price": round(prices[end_idx], 8),
        "return_pct": round(final_ret, 6),
    }


def _triple_barrier(
    prices: list[float],
    entry_indices: list[int],
    pt_pct: float,
    sl_pct: float,
    horizon: int,
    side: str,
) -> dict[str, Any]:
    validate_finite_floats(prices, name="prices")
    labels: list[dict[str, Any]] = []
    counts = {"profit_target": 0, "stop_loss": 0, "time_horizon": 0, "invalid": 0}
    for idx in entry_indices:
        r = _label_one_entry(prices, idx, pt_pct, sl_pct, horizon, side)
        labels.append({"entry_index": idx, **r})
        b = r.get("barrier") or "invalid"
        if b not in counts:
            counts["invalid"] += 1
        else:
            counts[b] += 1
    n = len(entry_indices)
    return {
        "n_entries": n,
        "labels": labels,
        "barrier_counts": counts,
        "pt_hit_rate": round(counts["profit_target"] / n, 4) if n else 0.0,
        "sl_hit_rate": round(counts["stop_loss"] / n, 4) if n else 0.0,
        "time_hit_rate": round(counts["time_horizon"] / n, 4) if n else 0.0,
    }


def _meta_labels(primary: list[int], pnl: list[float]) -> dict[str, Any]:
    validate_finite_floats(pnl, name="realized_pnl")
    n = min(len(primary), len(pnl))
    meta: list[int] = []
    correct = 0
    for i in range(n):
        # primary fires (non-zero), and realized PnL agrees with primary's sign → 1
        if primary[i] == 0:
            meta.append(0)
            continue
        if primary[i] > 0 and pnl[i] > 0:
            meta.append(1)
            correct += 1
        elif primary[i] < 0 and pnl[i] > 0:
            # short signal, positive PnL (price dropped) → correct
            meta.append(1)
            correct += 1
        else:
            meta.append(0)
    fired = sum(1 for p in primary[:n] if p != 0)
    return {
        "meta_labels": meta,
        "n_total": n,
        "n_signals_fired": fired,
        "n_correct": correct,
        "primary_accuracy": round(correct / fired, 4) if fired else None,
    }


def _bet_size_from_prob(
    prob: float, side: str, n_classes: int, step_size: float
) -> dict[str, Any]:
    """Lopez de Prado's signal: m = (p - 1/n_classes) / sqrt(p*(1-p))
    transformed through the standard-normal CDF, signed by side, then
    optionally discretized to a step_size grid.
    """
    if not (0.0 < prob < 1.0):
        return {"bet_size": None, "error": "probability must be in (0,1)"}
    sign = 1.0 if side == "long" else -1.0
    z = (prob - 1.0 / n_classes) / math.sqrt(prob * (1.0 - prob))
    raw = sign * (2.0 * _norm_cdf(z) - 1.0)
    if step_size > 0:
        steps = round(raw / step_size)
        size = steps * step_size
        # clamp into [-1, 1]
        if size > 1.0:
            size = 1.0
        elif size < -1.0:
            size = -1.0
    else:
        size = raw
    return {
        "bet_size": round(size, 6),
        "raw_signed_signal": round(raw, 6),
        "z_score": round(z, 6),
        "side": side,
        "n_classes": n_classes,
        "step_size": step_size,
    }


def _bet_size_average_active(
    sizes: list[float], active_windows: list[int]
) -> dict[str, Any]:
    """For each bar t, the active set is { i : i <= t < i + window_i }.
    Output[t] = mean(sizes[i] for i in active_set).
    """
    validate_finite_floats(sizes, name="bet_sizes")
    n = min(len(sizes), len(active_windows))
    if n == 0:
        return {"averaged": []}
    end = max(0, max((i + active_windows[i] for i in range(n)), default=0))
    out: list[float] = [0.0] * end
    counts: list[int] = [0] * end
    for i in range(n):
        w = max(1, active_windows[i])
        for t in range(i, min(i + w, end)):
            out[t] += sizes[i]
            counts[t] += 1
    averaged = [
        round(out[t] / counts[t], 6) if counts[t] > 0 else 0.0
        for t in range(end)
    ]
    return {
        "averaged": averaged,
        "n_bars": end,
        "max_overlap": max(counts) if counts else 0,
        "mean_overlap": round(sum(counts) / len(counts), 4) if counts else 0.0,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Apply the triple-barrier method (Lopez de Prado, AFML Ch.3) to "
            "label entries by whichever exits first: profit_target (label "
            "+1), stop_loss (-1), or time_horizon expiry (0). Caller "
            "supplies the price series, entry bar indices, PT/SL percentages "
            "and time horizon in bars. Useful for meta-labeling."
        )
    )
    async def label_triple_barrier(args: TripleBarrierArgs) -> dict:
        try:
            return {
                "ok": True,
                **_triple_barrier(
                    args.prices,
                    args.entry_indices,
                    args.profit_target_pct,
                    args.stop_loss_pct,
                    args.time_horizon,
                    args.side,
                ),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Given primary-signal labels (-1/0/+1) and realized per-bar PnL, "
            "produce meta-labels (1 = primary was right, 0 = primary was "
            "wrong or didn't fire). Use as the target variable for a "
            "secondary classifier trained on top of an existing strategy."
        )
    )
    async def label_meta_label_outcomes(args: MetaLabelArgs) -> dict:
        try:
            return {"ok": True, **_meta_labels(args.primary_signal, args.realized_pnl)}
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}

    @mcp.tool(
        description=(
            "Bet sizing from a classifier's predicted probability: "
            "size = sign × (2·Φ(z) − 1) where z = (p − 1/n) / sqrt(p·(1−p)). "
            "Optional step_size discretizes the output to a grid. "
            "Returns size in [-1, 1]."
        )
    )
    async def bet_size_from_probability(args: BetSizeProbArgs) -> dict:
        return {
            "ok": True,
            **_bet_size_from_prob(
                args.probability, args.side, args.n_classes, args.step_size
            ),
        }

    @mcp.tool(
        description=(
            "Average overlapping bet sizes to produce a smoothed target "
            "position per bar. Each bet at index i is active for "
            "active_windows[i] bars; output[t] = mean(bet_sizes[i] for "
            "i ≤ t < i+window_i). Useful to dampen whipsaw entries."
        )
    )
    async def bet_size_average_active(args: BetSizeAverageArgs) -> dict:
        try:
            return {
                "ok": True,
                **_bet_size_average_active(args.bet_sizes, args.active_windows),
            }
        except NumericValidationError as exc:
            return {"ok": False, "error": str(exc), "error_type": "NumericValidationError"}


__all__ = [
    "BetSizeAverageArgs",
    "BetSizeProbArgs",
    "MetaLabelArgs",
    "TripleBarrierArgs",
    "_bet_size_average_active",
    "_bet_size_from_prob",
    "_label_one_entry",
    "_meta_labels",
    "_triple_barrier",
    "register",
]
