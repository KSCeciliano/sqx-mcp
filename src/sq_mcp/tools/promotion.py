"""Formal strategy promotion workflow.

Promoting a strategy from "backtest looks good" to "running with real
money" deserves a gated checklist. This module encodes that gate as a
declarative pipeline: each step's pass/fail is reported, and the
overall promotion is approved only if all required gates pass.

A "promotion" is the act of moving a strategy from a Results databank
to live trading. The checks defend against:

- Backtests with too few trades to be statistically meaningful
- Strategies with no out-of-sample evidence
- Brittle strategies that depend on a few lucky trades
- Strategies whose drawdown exceeds the user's risk profile
- Strategies that haven't survived stress testing
- Strategies running on stale data

Tools:

- ``promotion_evaluate`` — given an inputs dict carrying everything
  the gates check (metrics, audit findings, drift verdict, stress
  verdict, risk profile), report each gate's pass/fail + the overall
  approval verdict.
- ``promotion_required_gates`` — list the default required gates so
  the caller knows what inputs to provide.
- ``promotion_explain_failure`` — given a failed promotion result,
  produce a human-readable explanation per failure.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---- argument schemas ------------------------------------------------------


class PromotionInputs(BaseModel):
    trades: int = Field(..., ge=0)
    fitness_oos: float | None = None
    oos_is_ratio: float | None = None
    drawdown_pct: float | None = Field(None, ge=0, le=100)
    profit_factor: float | None = None
    brittle_verdict: str | None = None  # "robust"|"skewed"|"brittle"|"very_brittle"
    drift_verdict: str | None = None  # "green"|"yellow"|"red"|"insufficient_sample"
    stress_verdict: str | None = None  # "robust"|"moderate"|"fragile"
    risk_profile: str = "moderate"  # ultra_conservative|conservative|moderate|aggressive
    days_since_build: float | None = None
    audit_critical_count: int = Field(0, ge=0)


class PromotionEvaluateArgs(BaseModel):
    strategy_name: str = Field(..., min_length=1, max_length=256)
    inputs: PromotionInputs


class PromotionExplainArgs(BaseModel):
    result: dict[str, Any]


# ---- helpers ---------------------------------------------------------------


# Map each risk profile to the drawdown cap the gate enforces.
PROFILE_DD_CAPS = {
    "ultra_conservative": 3.0,
    "conservative": 5.0,
    "moderate": 10.0,
    "aggressive": 20.0,
}

MIN_OOS_RATIOS = {
    "ultra_conservative": 0.6,
    "conservative": 0.5,
    "moderate": 0.4,
    "aggressive": 0.3,
}


def _gate(
    name: str,
    *,
    passed: bool,
    reason: str,
    required: bool = True,
    severity: str = "high",
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "required": required,
        "severity": severity,
        "reason": reason,
    }


def _evaluate(inputs: PromotionInputs) -> dict[str, Any]:
    profile = inputs.risk_profile if inputs.risk_profile in PROFILE_DD_CAPS else "moderate"
    dd_cap = PROFILE_DD_CAPS[profile]
    min_oos = MIN_OOS_RATIOS[profile]
    gates: list[dict[str, Any]] = []

    # 1. Sample size
    gates.append(_gate(
        "min_trades",
        passed=inputs.trades >= 100,
        reason=f"need ≥100 trades; have {inputs.trades}",
    ))

    # 2. OOS evidence
    if inputs.oos_is_ratio is None:
        gates.append(_gate(
            "oos_evidence",
            passed=False,
            reason="oos_is_ratio not provided — cannot verify OOS evidence",
        ))
    else:
        gates.append(_gate(
            "oos_evidence",
            passed=inputs.oos_is_ratio >= min_oos,
            reason=(
                f"oos_is_ratio={inputs.oos_is_ratio:.3f} (profile {profile} requires ≥{min_oos})"
            ),
        ))

    # 3. Drawdown within profile cap
    if inputs.drawdown_pct is None:
        gates.append(_gate(
            "drawdown_cap",
            passed=False,
            reason="drawdown_pct not provided",
        ))
    else:
        gates.append(_gate(
            "drawdown_cap",
            passed=inputs.drawdown_pct <= dd_cap,
            reason=(
                f"drawdown_pct={inputs.drawdown_pct:.2f}% (profile {profile} caps at {dd_cap}%)"
            ),
        ))

    # 4. Profit factor (soft gate)
    if inputs.profit_factor is not None:
        gates.append(_gate(
            "profit_factor",
            passed=inputs.profit_factor >= 1.2,
            required=False,
            severity="medium",
            reason=f"profit_factor={inputs.profit_factor:.3f} (target ≥1.2)",
        ))

    # 5. Brittleness
    if inputs.brittle_verdict:
        ok = inputs.brittle_verdict in {"robust", "skewed"}
        gates.append(_gate(
            "brittleness",
            passed=ok,
            reason=f"brittle_verdict={inputs.brittle_verdict} (must be robust or skewed)",
        ))

    # 6. Stress
    if inputs.stress_verdict:
        ok = inputs.stress_verdict in {"robust", "moderate"}
        gates.append(_gate(
            "stress",
            passed=ok,
            reason=f"stress_verdict={inputs.stress_verdict} (must be robust or moderate)",
        ))

    # 7. Drift (only enforced if drift data exists and is conclusive)
    if inputs.drift_verdict and inputs.drift_verdict != "insufficient_sample":
        ok = inputs.drift_verdict in {"green", "yellow"}
        gates.append(_gate(
            "drift",
            passed=ok,
            reason=f"drift_verdict={inputs.drift_verdict} (red blocks promotion)",
        ))

    # 8. Freshness
    if inputs.days_since_build is not None:
        gates.append(_gate(
            "freshness",
            passed=inputs.days_since_build < 365,
            reason=f"days_since_build={inputs.days_since_build:.1f}d (rebuild if ≥365d)",
        ))

    # 9. No critical audit findings
    gates.append(_gate(
        "audit_clean",
        passed=inputs.audit_critical_count == 0,
        reason=f"{inputs.audit_critical_count} critical audit finding(s) — none allowed",
    ))

    # Verdict
    required_failures = [g for g in gates if g["required"] and not g["passed"]]
    soft_failures = [g for g in gates if not g["required"] and not g["passed"]]
    approved = len(required_failures) == 0
    verdict = "approved" if approved else "blocked"
    return {
        "verdict": verdict,
        "approved": approved,
        "n_gates_total": len(gates),
        "n_gates_passed": sum(1 for g in gates if g["passed"]),
        "n_required_failures": len(required_failures),
        "n_soft_failures": len(soft_failures),
        "gates": gates,
        "blocked_by": [g["name"] for g in required_failures],
        "warnings": [g["name"] for g in soft_failures],
        "profile": profile,
    }


def _required_gates() -> list[dict[str, Any]]:
    return [
        {"name": "min_trades", "description": "≥100 trades", "required": True},
        {
            "name": "oos_evidence",
            "description": "oos_is_ratio above profile-specific minimum",
            "required": True,
        },
        {
            "name": "drawdown_cap",
            "description": "drawdown_pct ≤ profile cap",
            "required": True,
        },
        {
            "name": "profit_factor",
            "description": "PF ≥ 1.2 (soft, warning only)",
            "required": False,
        },
        {
            "name": "brittleness",
            "description": "brittle_verdict ∈ {robust, skewed}",
            "required": True,
        },
        {
            "name": "stress",
            "description": "stress_verdict ∈ {robust, moderate}",
            "required": True,
        },
        {
            "name": "drift",
            "description": "drift_verdict ∈ {green, yellow}; red blocks (if known)",
            "required": True,
        },
        {
            "name": "freshness",
            "description": "days_since_build < 365",
            "required": True,
        },
        {
            "name": "audit_clean",
            "description": "0 critical audit findings",
            "required": True,
        },
    ]


def _explain_failure(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"explanation": "unrecognised result payload"}
    gates = result.get("gates", [])
    failures = [g for g in gates if not g.get("passed")]
    if not failures:
        return {"explanation": "no gate failures — promotion was approved"}
    lines: list[str] = []
    for g in failures:
        sev = "REQUIRED" if g.get("required") else "soft"
        lines.append(f"- [{sev}] {g['name']}: {g['reason']}")
    return {
        "explanation": "Promotion blocked by:\n" + "\n".join(lines),
        "failure_count": len(failures),
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Run the full promotion gate checklist on a strategy. Each gate is "
            "evaluated against caller-supplied inputs (trades, oos_is_ratio, "
            "drawdown_pct, brittle_verdict, stress_verdict, drift_verdict, etc). "
            "Required gates block; soft gates only warn. Returns approved/blocked "
            "+ per-gate detail."
        )
    )
    async def promotion_evaluate(args: PromotionEvaluateArgs) -> dict:
        result = _evaluate(args.inputs)
        return {
            "ok": True,
            "strategy_name": args.strategy_name,
            **result,
        }

    @mcp.tool(
        description=(
            "List the default gates and which inputs they expect. Use to know "
            "what to provide for a promotion_evaluate call."
        )
    )
    async def promotion_required_gates() -> dict:
        return {"ok": True, "gates": _required_gates()}

    @mcp.tool(
        description=(
            "Explain why a promotion was blocked in human-readable form. "
            "Returns a multi-line markdown bullet list of failures."
        )
    )
    async def promotion_explain_failure(args: PromotionExplainArgs) -> dict:
        return {"ok": True, **_explain_failure(args.result)}
