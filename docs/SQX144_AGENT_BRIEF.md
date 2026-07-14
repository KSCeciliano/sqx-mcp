# SQX 144 Agent Brief

Purpose: a short operational brief for a specialized agent using `sqx-mcp` primarily against StrategyQuant X 144. This is not a replacement for `README.md`, `TOOLS.md`, `AGENT_GUIDE.md`, or `ARCHITECTURE.md`; it is the practical summary of what an agent should assume is reliable first.

## Recommendation

Use SQX 144 as the primary supported target.

Why:
- It is the best-validated version in this repository.
- The broadest tool surface has been exercised against it.
- Project/databank/CFX/analytics/MT5/pipeline workflows are all substantially validated on 144.

## What the agent should read first

In order:
1. `README.md`
2. `AGENT_GUIDE.md`
3. `TOOLS.md`
4. `CLAUDE.md`

This brief is only the fast operational layer.

## High-confidence families on SQX 144

These are good first-choice tool families for a specialized SQX agent:

### 1. Read-only inspection and parsing
- `sqx_inspect`
- `inspect_cfx`
- `project_inspect_cfx`
- `cfx_validate`
- `cfx_inspect`
- `cfx_compare_paths`
- `cfx_archetype`

Use these heavily. They are low-risk and highly useful.

### 2. Project and workspace operations
- `project_list`
- `project_start`
- `project_stop`
- `project_pause`
- `project_resume`
- `project_clone`
- `project_force_remove`
- `project_snapshot`
- `project_config`
- `workspace_overview`
- `workspace_disk_usage`
- `workspace_insights`
- `engine_status`
- `engine_log_tail`

### 3. Databank operations
- `databank_save`
- `databank_load`
- `databank_force_sync`
- `databank_clear`
- `databank_export`
- `databank_top_n`
- `databank_filter`
- `databank_snapshot_metrics`
- `databank_regression_check`
- `databank_correlation_matrix`
- `databank_promote`
- `databank_merge`
- `databank_partition`
- `databank_top_bottom`
- `databank_metric_histogram`
- `batch_audit_databank`
- `databank_audit_summary`
- `walkforward_databank_summary`

### 4. CFX mutation/configuration
- `cfx_set_fitness_criterion`
- `cfx_set_money_management`
- `cfx_set_genetic_options`
- `cfx_set_instrument`
- `cfx_set_trade_caps`
- `cfx_set_sl_pt_range`
- `cfx_set_setup_attrs`
- `cfx_toggle_building_blocks`
- `cfx_active_blocks`
- `cfx_list_building_blocks`
- `cfx_configure_walkforward`
- `cfx_configure_robustness`
- `cfx_inspect_robustness`
- `cfx_lint`
- `cfx_recommend_for_symbol`
- `cfx_recommend_settings`
- `cfx_compare_against_recommendation`
- `cfx_template_capture`
- `cfx_template_list`
- `cfx_template_apply`
- `cfx_template_delete`

### 5. Analytics / audit / portfolio math
This area is broad and largely usable on 144. Representative validated tools include:
- `stats_summary`
- `stats_autocorrelation`
- `stats_irr`
- `stats_jarque_bera`
- `stats_kurtosis`
- `stats_runs_test`
- `stats_skewness`
- `stats_time_weighted_return`
- `ratios_omega`
- `ratios_sharpe`
- `ratios_sortino`
- `ratios_calmar`
- `ratios_mar`
- `ratios_information`
- `ratios_pain_index`
- `ratios_ulcer_index`
- `tail_risk_historical_var`
- `tail_risk_cvar`
- `tail_risk_parametric_var`
- `tail_risk_tail_ratio`
- `tail_risk_gain_to_pain`
- `overfit_haircut_sharpe`
- `overfit_deflated_sharpe`
- `overfit_min_track_record_length`
- `overfit_pbo_from_oos_pairs`
- `benchmark_alpha_beta`
- `benchmark_compare_curves`
- `benchmark_excess_return_series`
- `benchmark_outperformance_periods`
- `portfolio_equal_weight`
- `portfolio_inverse_volatility`
- `portfolio_min_variance`
- `portfolio_risk_parity`
- `portfolio_diversification_ratio`
- `portfolio_ab_test`
- `portfolio_audit`
- `portfolio_capital_allocation`
- `portfolio_combined_equity`
- `portfolio_combined_equity_explicit`
- `portfolio_concentration`
- `portfolio_contribution`
- `portfolio_dedupe`
- `portfolio_diversity_score`
- `portfolio_export_csv`
- `portfolio_deck_markdown`
- `portfolio_review_bundle`

### 6. Workflow / promotion / shipping
- `promotion_required_gates`
- `promotion_evaluate`
- `promotion_explain_failure`
- `workflow_morning_briefing`
- `workflow_compare_two_strategies`
- `workflow_data_health_check`
- `workflow_promote_strategy`
- `ship_pipeline_dry_run`
- `ship_pipeline_status_summary`
- `pipeline_build_filter_retest`
- `strategy_export_pipeline`
- `pipeline_export_to_mt5`
- `ship_pipeline_run`

### 7. MT5 bridge
- `mt5_locate`
- `mt5_health_check`
- `mt5_list_experts`
- `mt5_list_indicators`
- `mt5_deploy_ea`
- `mt5_verify_deployment`
- `mt5_assign_magic_numbers`
- `mt5_patch_magic_in_source`
- `mt5_strategy_pack`
- `mt5_log_tail`
- `mt5_ea_generate_basic`
- `mt5_ea_generate_with_trailing`
- `mt5_ea_inject_risk_guardrails`
- `mt5_set_render`
- `mt5_set_parse`
- `mt5_set_merge`
- `mt5_set_diff`
- `mt5_set_apply_profile`

## Known caveats on SQX 144

These are not deal-breakers, but an agent should know them:

### Project status caveat
- `project_status` is fallback-heavy because SQX CLI may return `Not implemented`.
- Treat filesystem/log-derived status as normal, not as a plugin failure.

### Load/start caveat
- `project_load_and_start` can emit noisy `loadconfig` warnings on live projects.
- If start succeeds and the tool reports a warning path, do not assume full failure.

### On-disk vs JVM state
- Some databank behavior differs depending on whether strategies are only in JVM state or synchronized to disk.
- If results look incomplete, prefer `databank_force_sync` before concluding failure.

### Residual process issue
- Long sessions and repeated harnesses may leave:
  - `sqcli.exe -gui`
  - wrapper `python.exe`
- Before a fresh run tranche, cleanup is recommended.

## Recommended operating discipline for an agent

### Before starting build/retest work
1. `health_check`
2. `environment_health_check`
3. `workspace_overview`
4. `project_precheck`

### Before mutating CFX
1. `cfx_inspect`
2. `cfx_lint`
3. optional: `cfx_compare_against_recommendation`
4. apply `cfx_set_*`
5. verify again with `cfx_inspect`

### Before databank conclusions
1. prefer `databank_force_sync` if the project was recently running
2. then inspect with `databank_count`, `databank_top_n`, `databank_filter`, etc.

### Before MT5 deployment
1. `pipeline_export_to_mt5 deploy=False`
2. inspect plan
3. deploy only after verification
4. `mt5_verify_deployment`

## Practical trust model

On SQX 144, this MCP is strong enough for a specialized agent to do real work autonomously, but not with blind trust in every side effect.

Good model:
- trust read-only tools heavily
- trust analytics heavily
- trust project/databank/CFX tooling with verification after writes
- trust pipeline/MT5 flows with a verify-after-action pattern

Avoid this model:
- assume every engine-side operation succeeded merely because no exception was thrown

## Recommended scope for a specialized SQX agent

Good primary responsibilities:
- inspect and compare strategies
- curate databanks
- mutate and lint CFX projects
- run portfolio ranking/audit/selection
- perform promotion decisions
- prepare MT5 deployment plans
- generate and package MT5 artifacts
- monitor builds and summarize workspace health

## Notable non-goals / lower-confidence areas

Do not position the agent as:
- a guaranteed cross-version SQX operator for all old installs
- a replacement for every GUI-only SQX action
- a system that never needs verification after side-effecting commands

## Bottom line

If the agent is focused on SQX 144, the current repo documentation is complete enough, but it is broad.

This brief exists because a specialized agent benefits from a shorter operational contract:
- prefer 144
- use the validated high-confidence families first
- verify side effects
- expect occasional SQX CLI quirks
- use cleanup + sync discipline when needed
