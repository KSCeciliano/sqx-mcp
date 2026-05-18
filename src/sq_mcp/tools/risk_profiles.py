"""Risk-profile presets — conservative, moderate, aggressive.

Each profile bundles a recommended money-management method, risk per
trade, max drawdown threshold, max concurrent positions, and a few
ancillary knobs (compounding, max lots). The agent uses these to size
trades intelligently based on the user's stated tolerance, instead of
asking the user to fill in five different fields.

Profiles:

- ``conservative`` — 0.5% risk per trade, max 5% portfolio DD, max 2
  concurrent positions, no compounding. Suitable for users protecting
  capital or running on small accounts.
- ``moderate`` — 1.0% risk per trade, max 10% DD, 5 concurrent, fixed
  fractional. The default for most professional setups.
- ``aggressive`` — 2.0% risk per trade, max 20% DD, 10 concurrent,
  quarter-Kelly fractional. For users with proven edge and high risk
  tolerance.
- ``ultra_conservative`` — 0.25% risk per trade, max 3% DD, 1 position,
  fixed-size sub-Kelly. For prop firm challenges with strict DD rules.

Tools:

- ``risk_profile_recommend`` — pure-data lookup. Returns the full
  parameter bundle for a named profile. No engine call.
- ``risk_profile_apply`` — apply a profile's MM settings to a project's
  Build/Optimize tasks via cfx_set_money_management. Snapshots first.
- ``risk_profile_list`` — list all known profiles with their summary
  fields. Lets the agent show options to the user.
- ``risk_profile_compare_to_strategy`` — given a strategy's drawdown_pct
  and trade count, identify which profile's caps it would violate.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, field_validator

from sq_mcp._validation import ValidationError, validate_project_name
from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.cfx_config import _apply_to_cfx_buildlike, _patch_money_management_in_xml
from sq_mcp.tools.projects import _make_snapshot

# ---- profile definitions ---------------------------------------------------


PROFILES: dict[str, dict[str, Any]] = {
    "ultra_conservative": {
        "description": (
            "0.25% risk per trade. Max 3% portfolio drawdown. 1 concurrent "
            "position. Fixed-size sub-Kelly. Suited to prop firm challenges "
            "(FTMO, MyForexFunds) with strict daily/total DD rules."
        ),
        "risk_per_trade_pct": 0.25,
        "max_drawdown_threshold_pct": 3.0,
        "max_concurrent_positions": 1,
        "compounding": False,
        "recommended_kelly_fraction": 0.1,  # 10% of full Kelly
        "mm_method_type": "RiskFixedBalancePct",
        "mm_params": {
            "Risk": "0.25",
            "MaxLots": "1.0",
        },
        "min_oos_is_ratio": 0.5,
        "use_case": "Prop firm challenges, small accounts (<$5k), capital preservation",
    },
    "conservative": {
        "description": (
            "0.5% risk per trade. Max 5% portfolio drawdown. 2 concurrent "
            "positions. No compounding. Standard for users protecting "
            "real capital."
        ),
        "risk_per_trade_pct": 0.5,
        "max_drawdown_threshold_pct": 5.0,
        "max_concurrent_positions": 2,
        "compounding": False,
        "recommended_kelly_fraction": 0.15,
        "mm_method_type": "RiskFixedBalancePct",
        "mm_params": {
            "Risk": "0.5",
            "MaxLots": "2.0",
        },
        "min_oos_is_ratio": 0.5,
        "use_case": "Real money trading, small-to-mid accounts ($5k-$25k)",
    },
    "moderate": {
        "description": (
            "1.0% risk per trade. Max 10% portfolio drawdown. 5 concurrent "
            "positions. Fixed fractional. The professional default."
        ),
        "risk_per_trade_pct": 1.0,
        "max_drawdown_threshold_pct": 10.0,
        "max_concurrent_positions": 5,
        "compounding": True,
        "recommended_kelly_fraction": 0.25,
        "mm_method_type": "RiskFixedBalancePct",
        "mm_params": {
            "Risk": "1.0",
            "MaxLots": "5.0",
        },
        "min_oos_is_ratio": 0.4,
        "use_case": "Standard managed accounts, mid-to-large balances",
    },
    "aggressive": {
        "description": (
            "2.0% risk per trade. Max 20% portfolio drawdown. 10 concurrent "
            "positions. Quarter-Kelly fractional. Only with proven edge."
        ),
        "risk_per_trade_pct": 2.0,
        "max_drawdown_threshold_pct": 20.0,
        "max_concurrent_positions": 10,
        "compounding": True,
        "recommended_kelly_fraction": 0.25,
        "mm_method_type": "RiskFixedBalancePct",
        "mm_params": {
            "Risk": "2.0",
            "MaxLots": "10.0",
        },
        "min_oos_is_ratio": 0.4,
        "use_case": "Diversified portfolio of proven strategies, high risk tolerance",
    },
}


# ---- argument schemas ------------------------------------------------------


class RiskProfileRecommendArgs(BaseModel):
    profile: str

    @field_validator("profile")
    @classmethod
    def _v(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"unknown profile: {v} — choose from {list(PROFILES)}")
        return v


class RiskProfileApplyArgs(BaseModel):
    project: str
    profile: str
    target_first_only: bool = True
    set_initial_capital: float | None = Field(None, gt=0)

    @field_validator("project")
    @classmethod
    def _v_p(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("profile")
    @classmethod
    def _v_pr(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"unknown profile: {v}")
        return v


class RiskProfileCompareArgs(BaseModel):
    profile: str
    strategy_drawdown_pct: float = Field(..., ge=0, le=100)
    strategy_oos_is_ratio: float | None = Field(None, ge=0, le=10)
    strategy_trades: int | None = Field(None, ge=0)

    @field_validator("profile")
    @classmethod
    def _v(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"unknown profile: {v}")
        return v


# ---- helpers ---------------------------------------------------------------


def _profile_summary(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    out = dict(profile)
    out["name"] = name
    return out


def _compare_against(
    profile_name: str,
    profile: dict[str, Any],
    *,
    strategy_dd: float,
    oos_is_ratio: float | None,
    trades: int | None,
) -> dict[str, Any]:
    violations: list[str] = []
    if strategy_dd > profile["max_drawdown_threshold_pct"]:
        violations.append(
            f"max_drawdown_threshold_pct exceeded: "
            f"strategy={strategy_dd}%, profile_cap={profile['max_drawdown_threshold_pct']}%"
        )
    if (
        oos_is_ratio is not None
        and oos_is_ratio < profile["min_oos_is_ratio"]
    ):
        violations.append(
            f"min_oos_is_ratio violated: "
            f"strategy={oos_is_ratio}, profile_min={profile['min_oos_is_ratio']}"
        )
    if trades is not None and trades < 100:
        violations.append(
            f"strategy has only {trades} trades; profile recommends >= 100 for stable risk sizing"
        )
    verdict = "fits" if not violations else "violates"
    return {
        "profile": profile_name,
        "verdict": verdict,
        "violations": violations,
        "profile_caps": {
            "max_drawdown_threshold_pct": profile["max_drawdown_threshold_pct"],
            "min_oos_is_ratio": profile["min_oos_is_ratio"],
            "risk_per_trade_pct": profile["risk_per_trade_pct"],
        },
        "strategy_inputs": {
            "drawdown_pct": strategy_dd,
            "oos_is_ratio": oos_is_ratio,
            "trades": trades,
        },
    }


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Look up a risk profile by name (ultra_conservative, conservative, "
            "moderate, aggressive). Returns the full bundle: risk per trade %, "
            "max DD threshold, max concurrent positions, compounding flag, "
            "recommended MM method + params, recommended Kelly fraction. "
            "Pure data — no engine call."
        )
    )
    async def risk_profile_recommend(args: RiskProfileRecommendArgs) -> dict:
        prof = PROFILES[args.profile]
        return {"ok": True, **_profile_summary(args.profile, prof)}

    @mcp.tool(
        description=(
            "List every known risk profile with its summary fields. Useful when "
            "asking the user to pick one. Read-only."
        )
    )
    async def risk_profile_list() -> dict:
        return {
            "ok": True,
            "profiles": [_profile_summary(name, prof) for name, prof in PROFILES.items()],
        }

    @mcp.tool(
        description=(
            "Apply a risk profile's money-management settings to a project's "
            "Build/Optimize tasks. Snapshots the .cfx first. Activates the "
            "profile's MM method, sets Risk and MaxLots params, optionally "
            "updates initial_capital. WRITE — confirm with the user first."
        )
    )
    async def risk_profile_apply(args: RiskProfileApplyArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfx_path = eng.config.projects_dir / args.project / "project.cfx"
            if not cfx_path.is_file():
                return {"ok": False, "error": f"project.cfx not found: {cfx_path}"}
            snap = _make_snapshot(cfx_path, label=f"risk_profile_{args.profile}")
            prof = PROFILES[args.profile]

            def _patch(raw: bytes):
                new_bytes, counter, was_present, _available = _patch_money_management_in_xml(
                    raw,
                    method_type=prof["mm_method_type"],
                    params=prof["mm_params"],
                    initial_capital=args.set_initial_capital,
                )
                return new_bytes, counter, was_present

            summary, per_file = _apply_to_cfx_buildlike(
                cfx_path, target_first_only=args.target_first_only, patcher=_patch
            )
            return {
                "ok": True,
                "project": args.project,
                "profile": args.profile,
                "snapshot": str(snap),
                "applied_mm_method": prof["mm_method_type"],
                "applied_mm_params": prof["mm_params"],
                "summary": summary,
                "per_file": per_file,
                "next_steps": [
                    "verify in GUI or via cfx_inspect",
                    "consider cfx_set_max_strategies + cfx_set_genetic_options",
                    f"target max_drawdown_threshold_pct = {prof['max_drawdown_threshold_pct']}%",
                ],
            }
        except (EngineError, ValidationError) as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Check whether a strategy's drawdown / OOS-IS ratio / trade count fit "
            "within a given risk profile's caps. Returns 'fits' or 'violates' plus "
            "the specific cap violations. Use before promoting a strategy to live "
            "trading under a given profile. Pure data — no engine call."
        )
    )
    async def risk_profile_compare_to_strategy(args: RiskProfileCompareArgs) -> dict:
        prof = PROFILES[args.profile]
        return {
            "ok": True,
            **_compare_against(
                args.profile,
                prof,
                strategy_dd=args.strategy_drawdown_pct,
                oos_is_ratio=args.strategy_oos_is_ratio,
                trades=args.strategy_trades,
            ),
        }
