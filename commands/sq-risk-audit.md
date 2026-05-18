---
description: Run every risk-focused analysis tool on a project's databank in sequence — concentration, audit findings, distribution, diversity, regression.
argument-hint: <project> [databank=Results]
allowed-tools:
  - mcp__strategyquant__portfolio_summary
  - mcp__strategyquant__portfolio_audit
  - mcp__strategyquant__portfolio_concentration
  - mcp__strategyquant__portfolio_risk_metrics
  - mcp__strategyquant__portfolio_diversity_score
  - mcp__strategyquant__databank_correlation_matrix
---

The user wants a comprehensive risk audit of `$ARGUMENTS`.

Steps:

1. Parse: `project [databank]`. Default databank=Results.
2. Run in order, gathering insights:
   - `portfolio_summary` → baseline view
   - `portfolio_audit` → systemic findings (overfit rate, low profitable rate, etc.)
   - `portfolio_concentration` → HHI on trades_hash, symbol, TF, fingerprint
   - `portfolio_diversity_score` → 0-100 diversity rating
   - `portfolio_risk_metrics` → percentile breakdown of DD, fitness, return
   - `databank_correlation_matrix top_n=20` → equity-curve Pearson on the top 20
3. Surface, in this order:
   - Any **critical / high** findings from `portfolio_audit` (lead with these)
   - The diversity tier (excellent / good / marginal / poor)
   - The p95 drawdown_pct from risk_metrics (worst-case sizing)
   - Top correlated pairs (>0.85) from the correlation matrix
4. End with a recommendation. Examples:
   - "Diversity tier=poor + HHI on trades_hash > 0.5 → re-run portfolio_select_diverse or widen Builder blocks."
   - "Systemic overfit (high count of OOS/IS < 0.5) → reduce population/generations or widen IS sample."
   - "All clean → proceed to pipeline_export_to_mt5."

Do NOT run any write tool — this is read-only triage.
