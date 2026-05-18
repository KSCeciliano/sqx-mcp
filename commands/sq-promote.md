---
description: Run the full promote-to-live workflow on a strategy (audit gates → tag → lineage → alert). Dry-run by default.
argument-hint: <strategy_key> [risk_profile]
allowed-tools:
  - mcp__strategyquant__workflow_promote_strategy
  - mcp__strategyquant__promotion_evaluate
  - mcp__strategyquant__promotion_explain_failure
  - mcp__strategyquant__sqx_inspect
  - mcp__strategyquant__brittle_score_from_trades
  - mcp__strategyquant__stress_combined_scenarios
  - mcp__strategyquant__strategy_summarize
---

The user typed `/sq:promote <strategy_key> [risk_profile]`. Goal:
take the strategy from "candidate" to "promoted or blocked with
specific reasons", but never persist on the first call.

Steps:

1. **Parse args** — `strategy_key` (required, usually .sqx relpath or
   trades_hash), `risk_profile` (optional, default `moderate`).
2. **Gather promotion inputs**:
   - `sqx_inspect` on the strategy → metrics (trades, drawdown_pct,
     oos_is_ratio, profit_factor, fitness_oos, build_at)
   - `brittle_score_from_trades` if trade list is available
   - `stress_combined_scenarios` if trade list is available
   - Days since build = (now - build_at)
3. **Dry-run promotion**: call `workflow_promote_strategy` with
   `dry_run=true` and the assembled `promotion_inputs`.
4. **Report**:
   - If approved → show the manifest summary and ask the user "should I
     re-run with `dry_run=false` to commit?"
   - If blocked → call `promotion_explain_failure` and surface the
     reasons; suggest specific remediation per gate
     (e.g. "increase trade window", "reduce risk_per_trade").
5. **Wait for user confirmation** before running with `dry_run=false`.
   Confirmation should explicitly mention "commit", "ship", or "yes".

Do not skip the dry-run step. Promotion writes to the persistent state
store and lineage tree.
