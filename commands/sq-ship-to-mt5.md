---
description: Pick diversified top strategies from a databank, audit them, and ship the green-verdict ones to MT5 as a named pack.
argument-hint: <project> <databank> <pack_name> [N=5]
allowed-tools:
  - mcp__strategyquant__portfolio_select_diverse
  - mcp__strategyquant__strategy_export_pipeline
  - mcp__strategyquant__pipeline_export_to_mt5
  - mcp__strategyquant__mt5_locate
  - mcp__strategyquant__mt5_verify_deployment
---

The user wants to ship a portfolio from `$ARGUMENTS` to MetaTrader 5.

Steps:

1. Parse `$ARGUMENTS` — expected order: `project databank pack_name [N]`. Default N=5 if absent.
2. Call `pipeline_export_to_mt5` with `deploy=False` first to get the *plan*:
   - project, databank, pack_name, n
   - rank_mode='defensive' (composite-score), diversify_by='trades_hash'
   - min_trades=30, block_on=['critical', 'high']
3. Show the user the plan:
   - Green/yellow/red counts
   - Names of the EAs that will be deployed
   - Names of any excluded red-verdict ones (with reason)
4. Ask for explicit confirmation before re-running with `deploy=True`. This is a write to MT5/Experts/.
5. After deploy, call `mt5_verify_deployment` with the expected manifest to confirm files landed.
6. Remind the user MT5 must be restarted (or refreshed via F4 → Refresh) before the new EAs appear in the Navigator.

If `mt5_locate` returns "not found", abort early and tell the user to set MT5_HOME.
