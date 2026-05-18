"""High-level "ship to live" orchestrator.

Chains the most common production-promotion steps into a single,
well-audited pipeline so the agent doesn't have to wire them together
each time:

1. **Audit**: run the promotion gates on the strategy
2. **Tag**: if approved, attach the agreed tags
3. **Record lineage**: register the strategy node (optionally linking
   to a parent)
4. **Emit alert**: format a Slack/Discord notification with the
   outcome

The orchestrator does NOT touch MT5 (that's `pipeline_export_to_mt5`'s
job). It's the *book-keeping + decision* phase, not the file-write
phase.

Tools:

- ``ship_pipeline_run`` — full chain. Pass the strategy's inputs,
  proposed tags, and optional parent lineage ID. Returns a single
  manifest with each step's outcome.
- ``ship_pipeline_dry_run`` — same logic, but skip persistence
  (tagging, lineage). Useful to preview what the real run would do.
- ``ship_pipeline_status_summary`` — given the manifest a previous
  run produced, return a human-readable one-paragraph summary.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.alerts import _format_summary, _workspace_defaults
from sq_mcp.tools.lineage import _register
from sq_mcp.tools.promotion import PromotionInputs
from sq_mcp.tools.promotion import _evaluate as _evaluate_promotion
from sq_mcp.tools.state import _read_state, _state_path, _write_state_atomic
from sq_mcp.tools.tagging import _add_tags

# ---- argument schemas ------------------------------------------------------


class ShipPipelineArgs(BaseModel):
    strategy_name: str = Field(..., min_length=1, max_length=256)
    strategy_key: str = Field(..., min_length=1, max_length=256)
    promotion_inputs: PromotionInputs
    tags_if_approved: list[str] = Field(default_factory=list, max_length=20)
    parent_lineage_id: str | None = None
    dry_run: bool = False


class ShipManifestArgs(BaseModel):
    manifest: dict[str, Any]


# ---- helpers ---------------------------------------------------------------


def _run_pipeline(
    state: dict[str, Any],
    *,
    strategy_name: str,
    strategy_key: str,
    promotion_inputs: PromotionInputs,
    tags_if_approved: list[str],
    parent_lineage_id: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "strategy_name": strategy_name,
        "strategy_key": strategy_key,
        "dry_run": dry_run,
        "steps": {},
    }

    # 1. Promotion evaluation
    prom = _evaluate_promotion(promotion_inputs)
    manifest["steps"]["promotion"] = {
        "approved": prom["approved"],
        "blocked_by": prom["blocked_by"],
        "warnings": prom["warnings"],
        "gates": prom["gates"],
    }

    # 2. Tagging (only if approved + not dry-run)
    if prom["approved"] and tags_if_approved and not dry_run:
        tag_out = _add_tags(state, strategy_key, tags_if_approved)
        manifest["steps"]["tagging"] = tag_out
    else:
        manifest["steps"]["tagging"] = {
            "skipped": True,
            "reason": (
                "not approved"
                if not prom["approved"]
                else ("dry_run" if dry_run else "no tags requested")
            ),
        }

    # 3. Lineage register (only if approved + not dry-run)
    if prom["approved"] and not dry_run:
        lineage_out = _register(
            state,
            node_id=strategy_key,
            parent_id=parent_lineage_id,
            label=strategy_name,
            notes=None,
            metadata={"promotion_verdict": prom["verdict"]},
        )
        manifest["steps"]["lineage"] = lineage_out
    else:
        manifest["steps"]["lineage"] = {
            "skipped": True,
            "reason": "not approved" if not prom["approved"] else "dry_run",
        }

    # 4. Build an alert payload from workspace defaults
    alert_payload = {
        "drawdown_pct": promotion_inputs.drawdown_pct or 0,
        "oos_is_ratio": promotion_inputs.oos_is_ratio or 1,
        "trades": promotion_inputs.trades,
        "drift_verdict": promotion_inputs.drift_verdict or "green",
        "brittle_verdict": promotion_inputs.brittle_verdict or "robust",
        "status": "running",
    }
    from sq_mcp.tools.alerts import _evaluate_batch
    alert_eval = _evaluate_batch(_workspace_defaults(), alert_payload)
    manifest["steps"]["alerts"] = {
        "n_triggered": alert_eval["n_triggered"],
        "alerts": alert_eval["alerts"],
        "summary_markdown": _format_summary(alert_eval["alerts"], f"Ship report — {strategy_name}"),
    }

    # Overall verdict
    manifest["verdict"] = (
        "shipped"
        if prom["approved"] and not dry_run
        else "approved_dry_run"
        if prom["approved"] and dry_run
        else "blocked"
    )
    return manifest


def _status_summary(manifest: dict[str, Any]) -> str:
    if not isinstance(manifest, dict):
        return "unrecognised manifest"
    verdict = manifest.get("verdict", "unknown")
    name = manifest.get("strategy_name", "?")
    prom = manifest.get("steps", {}).get("promotion", {})
    blocked = prom.get("blocked_by", [])
    n_alerts = manifest.get("steps", {}).get("alerts", {}).get("n_triggered", 0)
    lines = [
        f"**Ship pipeline for {name}**",
        f"- Verdict: {verdict}",
        f"- Promotion gates blocked: {', '.join(blocked) if blocked else 'none'}",
        f"- Active alerts: {n_alerts}",
    ]
    return "\n".join(lines)


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Full ship-to-live pipeline: promotion gates → tag (if approved) → "
            "register lineage → emit alerts. Persists tagging + lineage via "
            "state.py. Returns a manifest with each step's outcome. Set "
            "dry_run=True to preview without persisting."
        )
    )
    async def ship_pipeline_run(args: ShipPipelineArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            p = _state_path(eng)
            state = _read_state(p)
            manifest = _run_pipeline(
                state,
                strategy_name=args.strategy_name,
                strategy_key=args.strategy_key,
                promotion_inputs=args.promotion_inputs,
                tags_if_approved=args.tags_if_approved,
                parent_lineage_id=args.parent_lineage_id,
                dry_run=args.dry_run,
            )
            if not args.dry_run:
                _write_state_atomic(p, state)
            return {"ok": True, "manifest": manifest}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Dry-run the ship pipeline: evaluate promotion, simulate tagging + "
            "lineage, return what *would* happen, but persist nothing. Use to "
            "preview a decision before committing."
        )
    )
    async def ship_pipeline_dry_run(args: ShipPipelineArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            state = _read_state(_state_path(eng))
            manifest = _run_pipeline(
                state,
                strategy_name=args.strategy_name,
                strategy_key=args.strategy_key,
                promotion_inputs=args.promotion_inputs,
                tags_if_approved=args.tags_if_approved,
                parent_lineage_id=args.parent_lineage_id,
                dry_run=True,
            )
            return {"ok": True, "manifest": manifest}
        except EngineError as e:
            return safe_error_payload(e)

    @mcp.tool(
        description=(
            "Produce a one-paragraph human-readable summary of a ship_pipeline "
            "manifest. Suitable for Slack / email."
        )
    )
    async def ship_pipeline_status_summary(args: ShipManifestArgs) -> dict:
        return {"ok": True, "summary": _status_summary(args.manifest)}
