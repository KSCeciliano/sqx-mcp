---
description: Statistically compare two strategies — paired t-test + brittle + stress + alpha/beta side-by-side.
argument-hint: <strategy_a> <strategy_b>
allowed-tools:
  - mcp__strategyquant__workflow_compare_two_strategies
  - mcp__strategyquant__hypothesis_paired_t_test
  - mcp__strategyquant__hypothesis_sign_test
  - mcp__strategyquant__hypothesis_wilcoxon_signed_rank
  - mcp__strategyquant__trade_csv_for_strategy
  - mcp__strategyquant__strategy_compare_two
---

The user typed `/sq:compare <strategy_a> <strategy_b>`. Goal: deliver a
defensible "A vs B" verdict, not just an eyeball comparison.

Steps:

1. **Resolve trades**: get per-trade PnL arrays for both strategies
   (via `trade_csv_for_strategy` if needed). They must be paired —
   same instrument, same period.
2. **Run `workflow_compare_two_strategies`** with the two trade
   arrays. It returns:
   - `paired_t_test`: t-statistic + p-value + verdict
   - `brittle_a` / `brittle_b`: per-strategy fragility verdicts
   - `stress_a` / `stress_b`: per-strategy stress verdicts
   - `summary`: one-line synthesized verdict
3. **If returns are also available** (curves provided), the tool also
   computes alpha/beta.
4. **Report**: a 4-section Markdown block:
   - "Verdict" — the synthesized one-line summary
   - "Statistical" — paired t-test numbers
   - "Brittleness" — per-strategy table
   - "Stress" — per-strategy table

If the p-value is borderline (0.04-0.06), recommend running the
non-parametric `sign_test` or `wilcoxon_signed_rank` as a sanity check.

Read-only. Do not promote anything.
