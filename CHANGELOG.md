# Changelog

All notable changes to this project will be documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.4.9] — security hardening (2026-05-18)

No new tools — pure robustness + security release. **+25 tests** (1571 passing, 6 skipped).

### Security

- **XXE / billion-laughs / SSRF defense across all XML parsing** (`src/sq_mcp/_xml.py`)
  - Replaced 29 `etree.fromstring()` / `etree.parse()` call sites in `parsers/cfx.py`, `parsers/sqx.py`, `tools/projects.py`, `tools/databanks.py`, `tools/robustness.py`, `tools/cfx_config.py`, `tools/cfx_diff.py`, `tools/cfx_lint.py`, `tools/cfx_advanced.py` with `safe_fromstring` / `safe_parse`.
  - Hardened defaults: `resolve_entities=False`, `no_network=True`, `load_dtd=False`, `huge_tree=False`. Fresh parser per call (lxml parsers are not thread-safe under MCP concurrency).
  - New `tests/test_xml_safety.py` proves the parser rejects classic XXE (file:// entity), billion-laughs amplification, and remote-DTD payloads while still parsing legitimate CFX/SQX XML.

### Robustness

- **NaN/Inf input validation** added to four high-impact math modules. Each helper now rejects non-finite floats at the boundary; each MCP wrapper catches `NumericValidationError` and returns `safe_error_payload(...)` instead of propagating to the transport.
  - `sensitivity.py` — 5 tools + new `_validate_points` / `_validate_scalar`; also guards the divide-by-zero in `_local_slope` when left/right neighbors share a parameter value.
  - `cost_model.py` — 4 tools; validates `trades`, `commission_per_trade`, `slippage_points`, `point_value`, `commission_grid`, `slippage_grid`, `average_trade`.
  - `brittle.py` — 4 tools; validates `trades` and `equity_curve`.
  - `benchmark.py` — 4 tools; validates `strategy_curve`, `benchmark_curve`, and the returns arrays.

- **Atomic file writes** for the remaining state-mutating sites.
  - `mt5_extra.py::mt5_patch_magic_in_source` — patched .mq5 now goes via `<name>.mq5.tmp` + `os.replace`; `.bak` is still written first.
  - `projects.py` — new `_atomic_write_bytes(path, data)` helper; all three CFX-archive write sites (`_rewrite_cfx_xml`, `_repoint_to_local_dat`, `cfx_patch_dates`) now write atomically. A killed process can no longer leave a half-written .cfx that SQ X would refuse to load on next start.

### Tests

- `+21` adversarial NaN/Inf cases across `test_sensitivity_helpers.py`, `test_cost_model_helpers.py`, `test_brittle_helpers.py`, `test_benchmark_helpers.py`.
- `+4` XML-safety regression tests in `test_xml_safety.py`.

## [0.4.8] — oscillators (2026-05-18)

### Added — 5 new tools across 1 new module, 13 new tests

**Classic momentum oscillators** (`oscillators.py`, 5 tools) — companion to `indicator_bands`
- `oscillator_rsi` — Wilder's Relative Strength Index; >70 = overbought, <30 = oversold.
- `oscillator_macd` — MACD = EMA(fast) − EMA(slow), with signal line and histogram. Default 12/26/9.
- `oscillator_stochastic` — %K + %D with overbought/oversold signal.
- `oscillator_cci` — Commodity Channel Index; unbounded, ±100 thresholds.
- `oscillator_williams_r` — Williams %R bounded [−100, 0].

Tool count: **477 → 482**; tests: **1533 → 1546 passing**.

## [0.4.7] — classic indicators round-out (2026-05-18)

### Added — 6 new tools across 1 new module, 14 new tests

Tool count went from **471 → 477**; test suite from **1519 → 1533 passing**. Closes the last gap I identified in the catalog: classic price-action indicators that strategies are built on top of.

**Indicator bands + ATR stops** (`indicator_bands.py`, 6 tools)
- `indicator_bollinger` — SMA ± N · stddev mean-reversion envelope with current `position_in_band` ∈ [0, 1].
- `indicator_keltner` — EMA ± N · ATR trend-following envelope; reacts faster than Bollinger when volatility expands.
- `indicator_donchian` — N-bar highest high / lowest low channel; the foundation of Turtle trend systems.
- `indicator_atr` — Wilder's smoothed Average True Range.
- `indicator_atr_stop` — entry ± n_atr · ATR stop level for a single trade.
- `indicator_chandelier_stop` — trailing stop variant: max_high_N − k·ATR for longs (less whipsaw than fixed-distance ATR stop).

## [0.4.6] — exposure tracking + signal quality (2026-05-18)

### Added — 10 new tools across 2 new modules, 26 new tests

Tool count went from **461 → 471**; test suite from **1493 → 1519 passing**. Closes the live-exposure gap (gross/net/leverage/concentration/correlation-weighted/VaR-decomposition over open positions) and the signal-research gap (Information Coefficient, IC decay, quantile sort, SNR, turnover) — the central pre-trade diagnostic kit in quantitative equity research.

**Live exposure tracking** (`exposure.py`, 5 tools)
- `exposure_summary` — net (long−short), gross (long+short), per-symbol breakdown, directional-bias verdict.
- `exposure_concentration_hhi` — Herfindahl-Hirschman on per-symbol weights with verdict (very_concentrated / concentrated / moderately_diversified / diversified).
- `exposure_leverage_check` — gross/net leverage vs caller's max-leverage policy with headroom % + verdict (safe / near_limit / breach / critical_breach).
- `exposure_correlation_risk` — correlation-weighted effective exposure: how much of gross exposure is correlated risk vs diversified (Σ w_i w_j ρ_ij)^½.
- `exposure_var_decomposition` — split portfolio VaR by symbol contribution using given correlations and per-symbol volatilities.

**Signal quality diagnostics** (`signal_quality.py`, 5 tools)
- `signal_information_coefficient` — Spearman rank correlation between signal and forward return + t-statistic + verdict (very_strong / strong / weak / noise). |IC| > 0.05 useful at scale.
- `signal_ic_decay` — IC across multiple horizons; identifies natural holding period (peak horizon) and where the signal decays to noise.
- `signal_hit_rate_by_quantile` — quintile/decile sort with mean forward return + hit rate per bucket; monotonicity check + top-bottom spread.
- `signal_to_noise_ratio` — variance ratio in dB with verdict (excellent >10dB / good 3-10 / marginal 0-3 / noise_dominated <0).
- `signal_turnover` — per-bar absolute change in the signal (normalized or raw); high turnover ⇒ high transaction costs.

## [0.4.5] — live monitoring + pairs + drawdown shape (2026-05-18)

### Added — 14 new tools across 3 new modules, 30 new tests

Tool count went from **447 → 461**; test suite from **1463 → 1493 passing**. Closes the live-monitoring gap (rolling Sharpe/DD/correlation/beta-alpha/win-rate to detect strategy decay early), the pairs-trading foundation (Engle-Granger cointegration + spread + z-score + N×N pair scan), and the drawdown-shape gap (per-episode duration/depth/recovery + time underwater + pain decomposition by depth bucket).

**Rolling-window live-monitoring metrics** (`rolling_metrics.py`, 6 tools)
- `rolling_sharpe` — annualized Sharpe over a trailing N-period window with current/min/max summary. Detects Sharpe decay live.
- `rolling_drawdown` — max drawdown in each rolling window; flags growing-pain windows.
- `rolling_volatility` — annualized stddev per window; live regime detection.
- `rolling_correlation` — Pearson between two series; detects when a diversifying strategy starts tracking the benchmark.
- `rolling_beta_alpha` — rolling regression of strategy on benchmark.
- `rolling_win_rate` — % positive trades per window; early edge-erosion warning.

**Drawdown shape analysis** (`drawdown_analysis.py`, 4 tools)
- `drawdown_periods` — segment the curve into episodes with start/peak/trough/recovery + depth_pct + duration + drop_bars + recovery_bars.
- `drawdown_time_underwater` — % of bars below the running peak with verdict (rarely / moderately / often / almost_always_underwater).
- `drawdown_recovery_curve` — recovery-time distribution across episodes.
- `drawdown_pain_decomposition` — total pain (area under underwater curve) split by depth bucket.

**Cointegration / pairs trading** (`cointegration.py`, 4 tools)
- `cointegration_engle_granger` — two-step test (OLS hedge ratio + heuristic ADF on residuals) with verdict at 10% / 5% / 1% critical levels.
- `cointegration_spread_series` — spread = y − β·x with OU half-life of the spread.
- `cointegration_zscore` — rolling-window z-score with signal (long_y_short_x / short_y_long_x / weak / none).
- `cointegration_pair_scan` — O(N²) pairwise scan of a series set; returns top-N most-cointegrated pairs by ADF statistic.

## [0.4.4] — execution + safety + breadth (2026-05-18)

### Added — 23 new tools across 5 new modules, 51 new tests

Tool count went from **424 → 447**; test suite from **1412 → 1463 passing**. Closes execution-side gaps (VWAP/TWAP/slippage), classic intraday trader reference (pivot points), information theory (entropy, KL, mutual information, transfer entropy), MCP-specific security (prompt-injection detector + PII redaction), and bar resampling between timeframes without re-importing data.

**Information theory** (`info_theory.py`, 5 tools)
- `info_shannon_entropy` — discretized entropy in bits; normalized to [0, 1] for direct interpretation.
- `info_kl_divergence` — Kullback-Leibler between two empirical distributions; Laplace-smoothed.
- `info_mutual_information` — MI between two series; captures non-linear dependence Pearson misses.
- `info_transfer_entropy` — Schreiber's directional information flow with caller-controlled lag.
- `info_diversity_index` — mean pairwise JS-like distance across a portfolio; verdict (highly_diversified / moderately_diversified / concentrated / near_duplicates).

**Pivot points** (`pivots.py`, 6 tools) — five canonical families
- `pivots_classic` / `pivots_fibonacci` / `pivots_camarilla` / `pivots_woodie` / `pivots_demark` — one tool per recipe.
- `pivots_compare_all` — run all five on the same bar side-by-side.

**VWAP / TWAP / execution metrics** (`vwap.py`, 5 tools)
- `vwap_compute` / `twap_compute` — volume- and time-weighted average prices.
- `vwap_slippage` — fill vs benchmark with bps + verdict (excellent / good / acceptable / poor).
- `vwap_participation_rate` — own / market volume; flags market-impact tier.
- `vwap_per_session` — split a tick stream into named sessions (asia/europe/us, custom) and compute VWAP per session (supports midnight-wrap).

**Prompt safety** (`prompt_safety.py`, 4 tools) — MCP-specific defenses
- `sanitize_text_for_llm` — escapes Markdown code fences and fake role tags so LLM doesn't interpret untrusted output as instructions.
- `sanitize_struct_for_llm` — recursive on nested dict/list/tuple payloads.
- `detect_prompt_injection` — heuristic pattern match (instruction overrides, destructive commands, role-tag impersonation, jailbreak phrases); severity + verdict.
- `redact_pii` — emails, IPv4, long hex (API keys), long digit runs.

**Bar resampling** (`bar_resampler.py`, 3 tools)
- `bar_resample_to_interval` — OHLCV aggregation to any new interval seconds.
- `bar_align_to_session` — align intraday bars to daily session boundaries starting at session_start_hour_utc.
- `bar_convert_timeframe` — M1/M5/M15/M30/H1/H4/D1/W1 → seconds + bars_per_target_bar compression ratio.

## [0.4.3] — QA-PRO parity + microstructure (2026-05-18)

### Added — 18 new tools across 4 new modules, 37 new tests

Tool count went from **406 → 424**; test suite from **1375 → 1412 passing**. Closes QuantAnalyzer's flagship System Parameter Permutation gap, adds high-frequency OHLC volatility estimators, AFML Ch.2 information-driven bar construction, and a Hurst/OU/CUSUM mean-reversion diagnostic suite.

**System Parameter Permutation** (`system_perm.py`, 4 tools) — QA's flagship robustness test
- `spp_permutation_grid` — generate the parameter-permutation grid (full enumeration or random subset) given base params + ±perturbation %.
- `spp_summarize_distribution` — distributional stats on per-permutation metrics + verdict (very_robust / robust / moderately_fragile / fragile).
- `spp_compare_baseline` — percentile rank of baseline against the permutation distribution; flags overfit when baseline is ≥95th percentile.
- `spp_recommend_params` — recommend the median-metric parameter combination (typical out-of-sample), not the in-sample best.

**OHLC volatility estimators** (`vol_estimators.py`, 6 tools)
- `vol_close_to_close` — baseline log-return stddev.
- `vol_parkinson` — Parkinson (1980); ~5× more efficient than C2C.
- `vol_garman_klass` — Garman-Klass (1980); ~7× more efficient, assumes zero drift.
- `vol_rogers_satchell` — Rogers-Satchell (1991); drift-independent.
- `vol_yang_zhang` — Yang-Zhang (2000); drift-independent, ~14× more efficient than C2C — the recommended high-frequency estimator.
- `vol_estimator_comparison` — run all five side-by-side.

**Information-driven bar construction** (`bar_construction.py`, 4 tools) — Lopez de Prado, AFML Ch.2
- `bar_construct_tick` — N-tick bars; captures order-arrival rate.
- `bar_construct_volume` — V-volume bars; captures liquidity.
- `bar_construct_dollar` — D-dollar bars; most stable across price regimes (recommended for crypto).
- `bar_construct_imbalance` — signed-volume imbalance bars; AFML "information bars" that emit when the order-flow imbalance crosses a threshold.

**Mean-reversion diagnostics** (`mean_reversion.py`, 4 tools)
- `mean_reversion_hurst` — Hurst exponent via rescaled-range (R/S); <0.5 = mean-reverting, ~0.5 = white noise / random walk increments, >0.5 = trending.
- `mean_reversion_ou_half_life` — Ornstein-Uhlenbeck half-life via lag-1 AR(1); how many bars a deviation takes to decay by 50%.
- `mean_reversion_cusum_detector` — two-sided CUSUM change-point detector for live PnL monitoring; flags upward / downward breaks against a reference mean.
- `mean_reversion_adf_heuristic` — heuristic Augmented Dickey-Fuller stationarity test re-exposed for general use; verdict at 10% / 5% / 1% critical levels.

## [0.4.2] — frontier quant techniques (2026-05-18)

### Added — 26 new tools across 7 new modules, 74 new tests

Tool count went from **381 → 406**; test suite from **1301 → 1375 passing**. Closes the biggest known gaps versus Lopez de Prado's *Advances in Financial Machine Learning* (Chs 3, 5, 7, 8) plus QuantAnalyzer's flagship WF Matrix and SPA test, plus three crypto-specific corrections that fix systematic backtest bias on perpetual futures.

**Triple-barrier labeling + bet sizing** (`labeling.py`, 4 tools)
- `label_triple_barrier` — apply profit-target / stop-loss / time-horizon barriers to entry indices on a price series; return per-entry label (+1 / -1 / 0) + which barrier hit + exit index/price/return.
- `label_meta_label_outcomes` — given primary-signal labels + realized PnL, produce meta-labels (1 = primary was right) for training a secondary classifier.
- `bet_size_from_probability` — Lopez de Prado's signed two-tailed CDF transform: `size = sign · (2·Φ(z) − 1)` with optional step-size discretization.
- `bet_size_average_active` — average overlapping bet sizes per bar to produce a smoothed target position (avoids whipsaw entries).

**Fractional differentiation** (`frac_diff.py`, 3 tools)
- `fracdiff_weights` — compute fixed-width-window weights via the Lopez de Prado recursion `ω_k = −ω_{k-1} · (d-k+1) / k`. d=1 collapses to first difference; d=0.5 preserves long memory.
- `fracdiff_apply` — convolve weights with a price series; returns the differentiated series + effective window.
- `fracdiff_find_min_d` — sweep d in [0.1, 0.95], use a heuristic Dickey-Fuller statistic to find the minimum d that achieves stationarity — preserves the maximum amount of predictive memory.

**Combinatorial Purged Cross-Validation** (`cpcv.py`, 3 tools)
- `cpcv_path_count` — pure combinatorics: how many distinct OOS paths CPCV generates for given (N groups, k test groups).
- `cpcv_generate_splits` — emit the schedule of (train, test) index splits with embargo to prevent train/test leakage.
- `cpcv_summarize_paths` — given an array of OOS Sharpes (one per CPCV path), report distribution percentiles + fraction beating a benchmark + verdict (robust / promising / borderline / fragile).

**Crypto perpetual-futures corrections** (`crypto_perp.py`, 5 tools)
- `perp_funding_adjusted_pnl` — subtract per-8h funding payments from longs, add them to shorts; flags a warning when funding > 10% of original PnL (the typical bias on a naive BTCUSDT perp backtest).
- `perp_liquidation_price` / `perp_liquidation_distance_pct` — compute liquidation price + distance for an isolated-margin position at given leverage + maintenance margin.
- `perp_liquidation_stress_test` — replay trades with intra-bar high/low excursion to detect which would have been liquidated at the given leverage. Returns per-trade hits + verdict (safe / marginal / dangerous / unfeasible).
- `perp_funding_regime_classifier` — bucket per-8h funding rates into contango / neutral / backwardation regimes + annualized funding cost estimate.

**Stationary bootstrap (Politis-Romano)** (`stationary_bootstrap.py`, 3 tools)
- `stationary_bootstrap_resample` — single resampled series via geometric-length blocks; preserves short-range autocorrelation.
- `stationary_bootstrap_paths` — bootstrap distribution of sum / mean / Sharpe across n paths; reports percentiles + fraction beating observed.
- `stationary_bootstrap_confidence` — bootstrap confidence interval for sum / mean / sharpe / median at the chosen confidence level.

**Walk-Forward Matrix + Efficiency** (`wf_matrix.py`, 4 tools)
- `wf_efficiency` — mean(OOS) / mean(IS) across paired folds + per-fold breakdown + verdict (excellent ≥ 0.8, good 0.6-0.8, marginal 0.4-0.6, overfit < 0.4, destructive < 0).
- `wf_matrix_summary` — sort a grid of (IS-bars, OOS-bars) results by efficiency; Markdown-ready table + best combo.
- `wf_anchored_vs_rolling` — compare expanding-window vs fixed-window WF results to recommend which suits the strategy.
- `wf_recommendation` — pick the (IS, OOS) combination that meets min-efficiency + min-OOS-bars criteria, weighted by sqrt(oos_bars) for statistical power.

**Hansen SPA / White Reality Check** (`spa_test.py`, 3 tools)
- `spa_test_reality_check` — multiple-testing-corrected p-value for "no strategy beats the benchmark" using stationary bootstrap; corrects for data-snooping when picking the best of N strategies.
- `spa_test_hansen` — Hansen's 2005 studentized variant; less sensitive to dispersion across irrelevant strategies.
- `spa_test_consistency` — bootstrap consistency report: how often is each strategy "the best" across bootstrap paths?

### Tooling

- `MAINTENANCE.md` — the 5-point playbook for module reorg, mypy strict adoption, profiling with py-spy, PyPy experiment, and PyO3 surgical extensions.
- `scripts/profile_tool.py` — invocable py-spy target with fixed workloads for the four likely bottlenecks (montecarlo, covariance, walkforward, brittle).
- `pyproject.toml` — added `[tool.mypy]` block with strict mode enabled per-module for the leaf math modules (`_numerics`, `ratios`, `tail_risk`, `stats_extra`, `overfit_diag`) — those pass `mypy --strict` clean.

## [0.4.1] — bulletproof inputs + agent onboarding (2026-05-18)

### Hardening: NaN / Inf rejection across priority modules

- New shared module `_numerics.py`: `validate_finite_floats`, `safe_div`, `safe_pct`, `percentile`, `mean`, `stddev`, `clamp`, `is_finite`. All raise `NumericValidationError` (subclass of `ValueError`) rather than propagating non-finite math.
- Wired `validate_finite_floats` into every priority helper: `ratios._sharpe / _sortino / _information_ratio / _omega / _pain_index / _ulcer_index`, `tail_risk._historical_var / _cvar / _parametric_var / _tail_ratio / _gain_to_pain`, `stats_extra._skewness / _kurtosis / _jarque_bera / _autocorrelation / _runs_test / _summary / _irr / _time_weighted_return`, `regime._classify_volatility / _classify_trend / _pnl_split`, `montecarlo._run_simulation / _probability_of_drawdown`, `stress_test` (validation wrapper), `portfolio_opt._equal_weight / _inverse_vol / _risk_parity / _min_variance / _covariance_matrix`.
- Pydantic-level finite guards on `calendar_effects.TradeWithTime.pnl`, `overfit_diag.DeflatedSharpeArgs` and `overfit_diag.PBOPair`.
- MCP tool layer in those modules now uniformly catches `NumericValidationError` and returns `{"ok": False, "error": ..., "error_type": "NumericValidationError"}` — adversarial inputs no longer propagate.
- `stress_test._metrics` now returns a fully-populated dict on empty input (no missing `win_rate` / `average_trade` keys).
- `tail_risk._historical_var.tail_size` rewritten to a clean generator (was a Python comprehension with two `if` clauses).

### Tests

- `tests/test_numerics_helpers.py` — 24 direct unit tests for the new shared helpers (NaN/Inf rejection, percentile interpolation, clamp edges, sample-vs-population stddev).
- `tests/test_adversarial_robustness.py` — 46 adversarial tests: parametrized NaN/Inf rejection across every priority helper, constant-series zero-variance edges, extreme-magnitude (±1e15) overflow checks, empty inputs, length-at-boundary, montecarlo seed determinism, calendar timestamp parsing failure, Pydantic-level finite guards.
- Total suite: **1301 passing**, 6 skipped (up from 1228).

### Documentation

- New `CLAUDE.md` at the repo root: 2-minute new-agent onboarding covering identity, architecture, patterns, hard invariants, runtime quirks (SQ X HTTP API), user profile, and the 60-second mental model for adding a tool. Self-contained — a fresh agent who reads only this file is immediately productive.

## [0.4.0] — hardening + frontier capabilities (2026-05-18)

### Added — 53 new tools across 11 new modules, 112 new tests

Tool count went from **328 to 381**; test suite from **1116 → 1228 passing**. Same atomic-snapshot, pure-Python (no numpy/scipy) discipline. Audit pass found no robustness or security regressions in the 0.3 codebase — every module's helpers are wrapped in safe error payloads, every state mutation uses atomic file writes, every user-supplied path goes through `resolve_safe_path`.

**Tail risk** (`tail_risk.py`, 5 tools)
- `tail_risk_historical_var` — non-parametric VaR at confidence c from a return series.
- `tail_risk_cvar` — Conditional VaR / Expected Shortfall — average loss in the tail.
- `tail_risk_parametric_var` — Normal-assumption VaR; compare with historical to gauge tail-fatness.
- `tail_risk_tail_ratio` — |p95| / |p5| ratio; >1 = fat upside.
- `tail_risk_gain_to_pain` — sum(gains) / |sum(losses)|.

**Overfitting diagnostics** (`overfit_diag.py`, 4 tools)
- `overfit_deflated_sharpe` — Bailey/Lopez de Prado DSR probability given N trials and return skew/kurtosis.
- `overfit_haircut_sharpe` — heuristic sqrt(log N / T) penalty.
- `overfit_pbo_from_oos_pairs` — Probability of Backtest Overfitting from IS/OOS Sharpe pairs.
- `overfit_min_track_record_length` — observations needed to claim Sharpe is statistically above benchmark.

**Regime detection** (`regime.py`, 4 tools)
- `regime_classify_volatility` — rolling stddev tertile classifier.
- `regime_classify_trend` — rolling regression-slope classifier (bear / neutral / bull).
- `regime_pnl_split` — PnL conditional on regime label at each trade.
- `regime_recommend_filter` — ranks regimes by chosen metric and recommends a trade/skip filter.

**Advanced statistics** (`stats_extra.py`, 8 tools)
- `stats_skewness` / `stats_kurtosis` — bias-corrected Fisher-Pearson moments.
- `stats_jarque_bera` — combined normality test with χ²(2) p-value.
- `stats_autocorrelation` — single-lag autocorrelation + Ljung-Box.
- `stats_runs_test` — Wald-Wolfowitz runs test on return signs.
- `stats_summary` — one-shot descriptive: mean, median, std, skew, kurt, quartiles.
- `stats_irr` — Newton-Raphson IRR from cashflows.
- `stats_time_weighted_return` — chained per-period TWR with annualization.

**Strategy fingerprint** (`fingerprint.py`, 4 tools)
- `fingerprint_from_metrics` / `fingerprint_from_sqx` — deterministic signature from params + curve digest.
- `fingerprint_similarity` — Jaccard(tags) + Pearson(curve) score with verdict.
- `fingerprint_find_duplicates` — groups near-duplicates by similarity threshold.

**Data health** (`data_health.py`, 5 tools)
- `data_health_gap_report` — gap detector vs expected bar interval.
- `data_health_freshness_gate` — pass/fail freshness check.
- `data_health_monotonicity` — strict-increase verification with anomaly indices.
- `data_health_bar_count_check` — actual vs expected with market-hours factor.
- `workflow_data_health_check` — combined gates with single verdict (healthy / stale / patchy / missing).

**Portfolio optimization** (`portfolio_opt.py`, 5 tools)
- `portfolio_equal_weight` / `portfolio_inverse_volatility` — baseline schemes.
- `portfolio_risk_parity` — iterative equal-risk-contribution solver.
- `portfolio_min_variance` — coordinate-descent long-only sum-to-1 min-variance.
- `portfolio_diversification_ratio` — Σ(w·σ) / σ_portfolio.

**Trade replay** (`trade_replay.py`, 5 tools)
- `trade_replay_apply_costs` — overlay commission/slippage on trade list.
- `trade_replay_position_sizing` — fixed_lot / fixed_fractional / percent_risk policies.
- `trade_replay_risk_caps` — per-trade, per-day, per-week loss caps with skip logic.
- `trade_replay_filter_by_regime` — skip trades in specified regimes.
- `trade_replay_compare_to_original` — metric delta side-by-side.

**Workspace operations** (`workspace_ops.py`, 4 tools)
- `workspace_clone_strategy` — copy a .sqx with dry-run guard.
- `workspace_archive_strategies` — atomic zip of a strategy set.
- `workspace_restore_archive` — extract with path-traversal filtering (drops `..` and `/`-containing entries).
- `workspace_batch_rename` — regex rename across a glob, refuses on collision.

**Pine Script generator** (`pinescript_gen.py`, 4 tools)
- `pinescript_generate_indicator` — SMA/EMA crossover, RSI, MACD scaffolds.
- `pinescript_generate_strategy` — full strategy with TP/SL/sizing/date filter.
- `pinescript_generate_alert_template` — alertcondition scaffold for TradingView webhooks.
- `pinescript_translate_mt5_basic` — best-effort MT5 → Pine translation with manual-review flag.

**MT5 .set / .ini files** (`mt5_set_files.py`, 5 tools)
- `mt5_set_parse` — parse to {section: {key: value}}.
- `mt5_set_diff` — added / removed / changed keys per section.
- `mt5_set_merge` — overlay onto base with overlay_wins control.
- `mt5_set_render` — round-trip back to .set text.
- `mt5_set_apply_profile` — apply ultra/conservative/moderate/aggressive risk preset.

### Hardening verified

- Engine HTTP transport: bounded retries, per-call timeout, call serialization, graceful shutdown ladder (exit → terminate → kill), URL encoding handles sqcli's no-decode quirk + Windows backslash.
- Atomic writes everywhere (tmp + `os.replace`) — confirmed in state, lineage, backup, archive.
- Path validation: `resolve_safe_path` on every file input; archive restore filters `..` traversal in zip member names.
- Pydantic schemas enforce bounded lengths and value ranges at the tool boundary.

## [0.3.0] — quantitative depth (2026-05-18)

### Added — 125 new tools, 28 new modules, 498 new tests

Tool count went from ~203 to **328**; test suite from ~618 to **1116 passing**. Same atomic-snapshot, pure-Python (no numpy/scipy) discipline as 0.2.

**Final-batch modules**: `hypothesis`, `ship_pipeline`, `cross_instrument`, `batch_audit`, `dashboard`, `workflows`.

**Workflow orchestrators** (`workflows.py`)
- `workflow_morning_briefing` — dashboard + alerts + schedule recommendations in one call.
- `workflow_promote_strategy` — full audit + ship pipeline with dry-run.
- `workflow_compare_two_strategies` — paired t-test + brittle + stress + alpha/beta side-by-side.

**Hypothesis testing** (`hypothesis.py`)
- `hypothesis_paired_t_test` / `hypothesis_sign_test` / `hypothesis_wilcoxon_signed_rank` / `hypothesis_unpaired_t_test` — formal statistical tests to compare two strategies' trade arrays. Pure-Python normal approximations; no scipy.

**Ship pipeline** (`ship_pipeline.py`)
- `ship_pipeline_run` / `ship_pipeline_dry_run` / `ship_pipeline_status_summary` — composite "audit → tag → register lineage → emit alerts" pipeline with persistence + dry-run mode + Markdown summary.

**Cross-instrument portfolio** (`cross_instrument.py`)
- `cross_instrument_group_by_symbol` / `cross_instrument_diversification_score` / `cross_instrument_recommend_caps` / `cross_instrument_asset_class_split` / `cross_instrument_correlation_groups` — diversification analysis across multiple symbols / asset classes / correlated clusters.

**Batch audit** (`batch_audit.py`)
- `batch_audit_databank` / `batch_audit_summary_markdown` — apply the promotion gates to every .sqx in a databank and produce a ranked queue (ready / marginal / blocked) plus a Markdown report.

**Dashboard** (`dashboard.py`)
- `dashboard_overview` / `dashboard_render_markdown` — one-shot workspace state view: project inventory, total strategies, tag inventory, lineage tree size, stale data, recent activity. Markdown-renderable for morning briefings.

**Additional new modules** (continuation): `brittle`, `cost_model`, `schedule`, `annual_report`, `ratios`, `calendar_effects`, `lineage`, `sensitivity`, `alerts`, `stress_test`, `promotion`, `benchmark`.

**Brittle-strategy detection** (`brittle.py`)
- `brittle_score_from_trades` / `brittle_curve_concentration` / `brittle_top_trades_share` / `brittle_consecutive_loss_streak` — Gini + Pareto + streak analysis to detect strategies whose P&L depends on a handful of outliers.

**Cost model** (`cost_model.py`)
- `cost_apply_to_trades` / `cost_recompute_metrics` / `cost_break_even_per_trade` / `cost_sensitivity_grid` — overlay realistic slippage + commission onto backtest, report degraded metrics, find the cost ceiling, sweep a grid.

**Schedule recommender** (`schedule.py`)
- `schedule_for_strategy` / `schedule_for_workspace` / `schedule_quarterly_calendar` — heuristic "no_action / retest / rebuild" based on data freshness, live drift, build age. Asset-class-aware yearly cadence calendar.

**Annual report builder** (`annual_report.py`)
- `annual_report_compose` + per-section helpers (top strategies, drift summary, recent activity) — full Markdown reports composed from caller-supplied inputs.

**Risk-adjusted ratios** (`ratios.py`)
- `ratios_sharpe` / `ratios_sortino` / `ratios_calmar` / `ratios_mar` / `ratios_information` / `ratios_omega` / `ratios_pain_index` / `ratios_ulcer_index` — explicit-input variants of every common risk-adjusted return measure (pure Python).

**Calendar effects** (`calendar_effects.py`)
- `calendar_by_hour` / `calendar_by_dayofweek` / `calendar_by_month` / `calendar_by_quarter` / `calendar_session_split` — per-bucket PnL aggregates from a timestamped trade list. Supports custom sessions that wrap midnight.

**Strategy lineage** (`lineage.py`)
- `lineage_register` / `lineage_link` / `lineage_ancestors` / `lineage_descendants` / `lineage_tree` / `lineage_get` / `lineage_list` / `lineage_remove` — track parent → child relationships between strategy iterations. Cycle-safe, atomic persistence via `state.py`.

**Parameter sensitivity** (`sensitivity.py`)
- `sensitivity_local_slope` / `sensitivity_plateau_score` / `sensitivity_range_decay` / `sensitivity_compare_two_params` / `sensitivity_bullseye` — score how robust a strategy's metric is across a parameter sweep; find safety plateaus.

**Declarative alert rules** (`alerts.py`)
- `alert_rule_evaluate` / `alert_rules_batch` / `alert_workspace_defaults` / `alert_format_summary` — evaluate `{metric, op, threshold, severity}` rules against arbitrary payloads; curated workspace defaults; Slack-/Discord-ready Markdown summaries.

**Stress testing** (`stress_test.py`)
- `stress_apply_slippage` / `stress_apply_skip_best` / `stress_apply_random_skip` / `stress_worst_case_dd` / `stress_combined_scenarios` — perturb a strategy under several adversarial conditions and return a robust/moderate/fragile verdict.

**Promotion workflow** (`promotion.py`)
- `promotion_evaluate` / `promotion_required_gates` / `promotion_explain_failure` — formal gated checklist for "ready to go live" decisions: min trades, OOS evidence, drawdown cap per profile, brittleness, stress, drift, freshness, audit cleanliness.

**Benchmark comparison** (`benchmark.py`)
- `benchmark_compare_curves` / `benchmark_alpha_beta` / `benchmark_excess_return_series` / `benchmark_outperformance_periods` — strategy vs buy-and-hold / index: alpha, beta, excess return, longest outperformance streak.

### Initial 0.3 capability expansion (also in this release)

**New modules:** `position_sizing`, `montecarlo`, `clustering`, `walkforward`, `symbol_intel`, `notify`, `risk_profiles`, `mt5_ea_template`, `performance_attribution`, `tagging`, `drift`.

**Position sizing & risk math** (`position_sizing.py`)
- `position_sizing_calc` — risk-percent lot sizing for crypto / forex / generic instruments, including notional + margin reports.
- `kelly_criterion` — full Kelly + half / quarter-Kelly with sanity-check interpretation.
- `position_sizing_pyramid` — cumulative size, weighted entry and R-multiple of each tier in a pyramiding plan.
- `position_sizing_risk_of_ruin` — analytic risk-of-ruin estimate from win rate, payoff and risk per trade.

**Monte Carlo simulation** (`montecarlo.py`)
- `montecarlo_equity_paths` — bootstrap N paths from a strategy's .sqx equity sparkline.
- `montecarlo_drawdown_distribution` — percentile distribution of max-DD across paths.
- `montecarlo_probability_of_drawdown` — probability of hitting an X% drawdown.
- `montecarlo_synthetic_from_returns` — bootstrap from an explicit returns array (MT5 report, custom CSV).

**Clustering & similarity** (`clustering.py`)
- `strategy_cluster_kmeans` — Lloyd's k-means on z-scored metric vectors, returns cluster assignments + centroids.
- `strategy_pairwise_distance` — pairwise Euclidean distance matrix on top-N strategies.
- `strategy_nearest_neighbors` — K nearest neighbors of a target strategy by metric distance.

**Walk-forward analysis** (`walkforward.py`)
- `walkforward_databank_summary` — fold-by-fold IS/OOS table + WF efficiency from a WF databank.
- `walkforward_consistency_score` — 0-100 robustness score (signal-to-noise, positive-fold fraction, IS→OOS slippage).
- `walkforward_from_folds` — same math from an explicit folds array.
- `walkforward_overfit_flags` — list folds where OOS < ratio × IS.

**Symbol intelligence** (`symbol_intel.py`)
- `symbol_normalize` — alias forms for a symbol (BTCUSDT → BTC/USDT, BTC-USDT, …).
- `symbol_timeframe_matrix` — per-symbol presence matrix for the requested timeframes.
- `symbol_freshness_report` — flag .dat files older than N days.
- `symbol_data_summary` — single-symbol deep dive (timeframes, sizes, ages, header strings).
- `symbol_coverage_gaps` — return the (symbol, timeframe) pairs the user wants but doesn't have.

**Notifications** (`notify.py`)
- `notify_format_slack_build_status` / `notify_format_discord_build_status` — ready-to-post Slack markdown / Discord embed from a project_status dict.
- `notify_format_audit_summary` — counts by severity + top-10 findings as Slack/Discord block.
- `notify_format_email_html` — HTML email body with sections and lists, with HTML escaping.
- `notify_format_status_line` — one-line plain-text status for cron logs.

**Risk profiles** (`risk_profiles.py`)
- `risk_profile_recommend` / `risk_profile_list` — named profiles (ultra_conservative / conservative / moderate / aggressive) with risk%/DD-cap/MM-method bundle.
- `risk_profile_apply` — apply a profile's MM settings to a project's Build/Optimize tasks.
- `risk_profile_compare_to_strategy` — check whether a strategy's drawdown / OOS-IS ratio / trade count fits within a profile's caps.

**MT5 EA scaffolding** (`mt5_ea_template.py`)
- `mt5_ea_generate_basic` — minimal but production-shaped .mq5 with risk-percent sizing, magic, SL/TP in points, max-lot cap.
- `mt5_ea_generate_with_trailing` — adds break-even + trailing stop.
- `mt5_ea_inject_risk_guardrails` — idempotently inject Risk%/MaxLots/MagicNumber inputs + LotSizeFromRisk helper into an arbitrary .mq5 source.

**Performance attribution** (`performance_attribution.py`)
- `attribution_by_strategy` — contribution = weight × return; per-strategy share of total.
- `attribution_by_month_from_curves` — bucket equity curves into M segments, report per-bucket returns and weighted portfolio buckets.
- `attribution_regime_split` — split buckets into calm vs volatile; per-strategy return in each regime.
- `attribution_concentration_index` — Herfindahl-style 0-100 concentration of return contributions.

**Strategy tagging** (`tagging.py`)
- `strategy_tag_add` / `strategy_tag_remove` / `strategy_untag` — attach/remove arbitrary labels to a strategy_key (typically .sqx relpath).
- `strategy_query_by_tag` (OR) / `strategy_query_by_tag_all` (AND).
- `strategy_tag_list_all` — full strategy↔tag inverse map.
- `strategy_tag_rename` — canonicalize spelling everywhere a tag appears.

**Live drift detection** (`drift.py`)
- `drift_compare_win_rate` — Wilson CI on live win rate; flag if backtest rate falls outside band.
- `drift_compare_average_trade` — z-score of live mean trade PnL vs backtest mean.
- `drift_compare_distributions` — two-sample Kolmogorov-Smirnov distance (pure Python, no scipy).
- `drift_compare_drawdown` — flag if live max-DD exceeds backtest by threshold.
- `drift_score_overall` — combine into a green/yellow/red verdict + recommendation.

### Cross-metric correlation

- `databank_metric_correlation` (added to `portfolio_risk.py`) — Pearson correlation between any two metrics across a databank's strategies.

### Engineering notes

- All new modules follow the existing pattern: pure-Python math, atomic file writes for any state, Pydantic input schemas, snapshot-before-patch where they write to .cfx.
- Drift tools require >=20 live trades before rendering a verdict — defensive against acting on noise.
- Monte Carlo uses bootstrap resampling with replacement; seed is exposed for reproducibility.
- Magic numbers in mt5_ea_template inputs are validated against MT5's 2³¹ limit.
- Risk profiles use SQ X-native MM method names (RiskFixedBalancePct, …) so they apply via the existing cfx_config patcher.

## [0.2.0] — capability expansion (2026-05-18)

### Added — 115 new tools, 39 new modules, 365+ new tests

Tool count went from ~88 to **203**; test suite from ~253 to **618 passing**. All modules lint-clean (`ruff check src/ tests/`). Includes end-to-end integration tests with synthetic .sqx fixtures.

**New modules:** `advisor`, `analytics`, `backup`, `batch`, `cfx_advanced`, `cfx_diff`, `cfx_lint`, `cfx_templates`, `comparison`, `data_quality`, `databank_partition`, `engine_watchdog`, `integrity`, `meta`, `mt5_extra`, `mt5_reports`, `passthrough`, `pipeline`, `pipeline_mt5`, `portfolio`, `portfolio_audit`, `portfolio_risk`, `presets`, `regression`, `reports`, `state`, `strategy_inspect`, `trade_analysis`, `visualize`.

**New slash commands** (`commands/`): `sq:ship-to-mt5`, `sq:portfolio-deck`, `sq:rebuild-and-compare`, `sq:health`, `sq:cfx-lint`, `sq:bootstrap`.

**New top-level docs:** [ARCHITECTURE.md](ARCHITECTURE.md) (module map + design patterns), regenerated [TOOLS.md](TOOLS.md) (per-category catalog).

**Portfolio analytics** (`portfolio.py`, `portfolio_risk.py`, `comparison.py`, `analytics.py`)
- `portfolio_dedupe`, `portfolio_rank`, `portfolio_summary`, `portfolio_select_diverse` — dedupe by trades_hash, defensive composite ranking, diversified top-N.
- `portfolio_ab_test`, `databank_correlation_matrix` — compare two databanks, Pearson correlation on equity sparklines.
- `portfolio_risk_metrics`, `portfolio_concentration`, `portfolio_capital_allocation` — distributional stats, Herfindahl-Hirschman concentration, inverse-DD weighted allocation.
- `portfolio_combined_equity`, `portfolio_combined_equity_explicit` — synthesize a portfolio equity curve from N strategies.

**CFX project authoring** (`cfx_config.py`, `cfx_advanced.py`)
- `cfx_set_fitness_criterion`, `cfx_set_money_management`, `cfx_set_data_range`, `cfx_inspect` — surgical patches to fitness, MM and date range.
- `cfx_set_trade_caps`, `cfx_set_sl_pt_range`, `cfx_set_genetic_options`, `cfx_set_max_strategies` — deeper Builder settings (max trades/day, SL/PT range, population size, MaxGenerations, IS/OOS ratio, MaxStrategies + paired StopCondition).
- `cfx_list_building_blocks`, `cfx_toggle_building_blocks` — enumerate and toggle individual indicator/operator blocks by key prefix or category.

**Iteration-over-iteration regression** (`regression.py`)
- `databank_snapshot_metrics`, `databank_regression_check` — atomic JSON snapshots + threshold-based green/yellow/red regression detection across runs.

**Strategy-level inspection** (`strategy_inspect.py`, `pipeline.py`)
- `strategy_compare_two`, `strategy_summarize`, `strategy_equity_curve_stats` — side-by-side metric diff, traffic-light verdict, drawdown/recovery/hit-ratio geometry.
- `strategy_export_pipeline`, `strategy_ready_for_deploy` — chain rank → diversify → audit with deploy-readiness verdict.

**MT5 deployment** (`mt5_extra.py`, `pipeline_mt5.py`)
- `mt5_assign_magic_numbers`, `mt5_patch_magic_in_source`, `mt5_verify_deployment`, `mt5_strategy_pack` — deterministic collision-free magic numbers, surgical regex patching of EA source, SHA-256 deployment verification, named pack folder with manifest.json.
- `pipeline_export_to_mt5` — mega-tool that chains databank → diversified picks → audit → magic-assign → optional file deploy with a JSON manifest. `deploy=False` returns the plan; `deploy=True` writes to MQL5/Experts/.

**Data integrity & quality** (`integrity.py`, `data_quality.py`)
- `broker_data_integrity`, `history_disk_inventory` — cross-check SQ's SQLite registry against on-disk .dat files.
- `data_bar_density`, `data_workspace_quality_report`, `data_dat_header_diff`, `data_age_check` — bar-count coverage analysis, workspace-wide stale/orphaned data audit, header diff for verifying incremental pulls.

**Workspace orchestration** (`batch.py`)
- `projects_batch_status`, `projects_batch_start`, `projects_batch_stop`, `projects_inventory`, `workspace_overview` — multi-project status grid, capacity-aware batch start (engine doesn't enforce concurrency itself), workspace-wide inventory.

**Meta + health** (`meta.py`)
- `tools_catalog`, `environment_health_check`, `env_path_tools_check` — self-describing tool catalog with category filter, full-environment audit (SQ X install, sqcli binary, engine port TCP probe, data.db, MT5 install, Python dep imports), shell-tools PATH check.

### Engineering notes

- Every write tool auto-snapshots the .cfx via `_make_snapshot()` before patching; atomic file swap via tmp file + `os.replace()`.
- All patchers are idempotent (zero changes when target value already matches; explicit noop reporting).
- Money-management patcher only updates params that already exist — never injects new Param entries to avoid corrupting MM blocks.
- Magic-number assignment uses SHA-256(namespace:name) hashed into [floor, 2³¹]; namespace lets the same EA get different magic per account.
- Pearson correlation on equity-curve returns (not raw values) so curves of different magnitudes still compare cleanly.
- Composite ranking penalizes overfit (low OOS/IS), deep DD (>25%), and small samples (<100 trades).

## [0.1.0] — initial implementation

### Added

- **MCP server core** (`src/sq_mcp/server.py`) — FastMCP server with lifespan-managed engine.
- **Engine layer** (`engine.py`) — spawns `sqcli -gui` once and keeps it alive; HTTP client to `:5050`; noise-filtered stdout consumer; bounded retries; lock-serialized calls; graceful shutdown.
- **File parsers** (`parsers/mq5.py`, `parsers/sqx.py`, `parsers/cfx.py`) — pure-Python static analysis of MQL5 Expert Advisors, `.sqx` strategy archives and `.cfx` project configs.
- **Risk-rule engine** for MQL5 — detects `NO_STOP_LOSS`, `NO_PROFIT_TARGET`, `VERY_SHORT_HOLD`, `DEFAULT_MAGIC_NUMBER`, `HIGH_MAX_LOTS`, `MM_FIXED_AMOUNT_NO_SL`, `VERIFY_OOS`. Crypto symbols escalate `NO_STOP_LOSS` to `critical`.
- **Active build monitor** (`monitor.py`) — background asyncio task per project that polls fitness, count and status; evaluates `STALLED_GROWTH`, `LOW_MEDIAN_FITNESS`, `OOS_DEGRADATION`, `PROJECT_NOT_RUNNING`; can auto-stop a wasteful build.
- **30+ MCP tools** across `tools/{projects,databanks,data,symbols,analysis,monitor}.py`, each with Pydantic input schemas and structured error returns.
- **Input validation** (`_validation.py`) — strict project / databank / symbol / timeframe / date / path validators run at every tool boundary; blocks argument injection.
- **6 slash commands** (`commands/`): `/sq:analyze-mq5`, `/sq:test-strategy`, `/sq:walk-forward`, `/sq:monitor-build`, `/sq:portfolio-build`, `/sq:data-fix`.
- **4 subagents** (`agents/`): `sq-strategy-reviewer`, `sq-build-watcher`, `sq-data-curator`, `sq-portfolio-architect`.
- **Plugin packaging** — `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.mcp.json` for Claude Code marketplace install.
- **Distribution** — `pyproject.toml` for PyPI publishing; `uvx sq-mcp@latest` install path.
- **Documentation** — `README.md`, `docs/INSTALL.md`, `docs/TOOLS.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md`, `examples/`.
- **Test suite** — 131 tests across 7 files; 125 pass, 6 skipped (require live SQ X / fixture env vars). Includes a synthetic-fixture suite that catches each historical bug.
- **Integration smoke harness** (`scripts/integration_smoke.py`) — drives every tool category end-to-end against a live SQ X.

### Fixed during initial development

- **Bug 1**: `mq5` parser captured `bool LongEntrySignal = false;` declaration instead of the actual rule body. Now scoped to the `// Rule: Trading signals` block.
- **Bug 2**: `mq5` parser flagged false-positive SL/PT presence by picking up assignments in helper code (`sl = HistoryOrderGetDouble(...)`). Now scoped to entry rule blocks only.
- **Bug 3**: engine fired "ready" on early JVM logs, leading to `Error: CLI not ready.` from every subsequent call. Now waits for `HTTP API started` and probes with a real CLI command until it returns valid output.
- **Bug 4**: sqcli HTTP API doesn't decode `+` → space or `%3D` → `=`. Now hand-roll URL with `_encode_cmd()` (only encodes characters that must be encoded for HTTP transit).
- **Bug 5**: `databank action=count` returns `Error: Not implemented.` in Build 143. Now falls back to `databank action=list` + client-side count, both in the `databank_count` tool and the monitor.

### Windows compatibility (0.1.1)

- **Fix W1** — `engine.py`: spawn `sqcli.exe` with `creationflags=CREATE_NO_WINDOW`. Without this flag the JVM pops a visible console window on every server start.
- **Fix W2** — `engine.py` `_encode_cmd()`: convert `\` to `/` before URL-encoding. SQ X is Java-based and accepts forward-slash paths on every OS; leaving native Windows paths literal causes httpx to %5C-encode them, which sqcli does not URL-decode.
- **Fix W3** — `config.py`: `shutil.which()` lookup now uses platform-correct executable name (`sqcli.exe` on Windows). The previous implicit `PATHEXT` lookup worked but was fragile.
- **Doc** — `engine.py` shutdown chain comment: clarified that on Windows `proc.terminate()` and `proc.kill()` both call `TerminateProcess`, so the second escalation is redundant but harmless.
- **Tests** — added 4 new tests in `test_engine_helpers.py` for `_encode_cmd` covering: literal `=` / `-`, `%20` (not `+`) for space, Windows backslash → forward slash, quoted strategy-list round-trip. 129 tests pass, 6 skipped, ruff clean.

### Known limitations

- `.sqx` `orders.bin` and `dailyEquity.bin` are Java-serialized; not parsed externally.
- Result Plugin SDK (Java) is out of scope.
- Linux Fedora 43 verified end-to-end. Windows fixes are code-correct and unit-tested but not yet exercised against a live Windows install — needs a Windows tester to confirm `CREATE_NO_WINDOW` actually suppresses the JVM console and that path interpolation reaches sqcli intact.
- macOS install paths configured but unverified.
- `databank_load` / `data_import` round trips not run end-to-end (would mutate workspace / require external data).
