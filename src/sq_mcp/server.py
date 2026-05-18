"""FastMCP server for StrategyQuant X.

Run with `sq-mcp` after `pip install sq-mcp` (or `uvx sq-mcp@latest`).

Stdio transport: stdout is reserved for JSON-RPC, all logs MUST go to stderr.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from sq_mcp.config import detect_config
from sq_mcp.engine import EngineClient
from sq_mcp.tools import (
    advisor,
    alerts,
    analysis,
    analytics,
    annual_report,
    audit,
    backup,
    bar_construction,
    bar_resampler,
    batch,
    batch_audit,
    benchmark,
    brittle,
    calendar_effects,
    cfx_advanced,
    cfx_config,
    cfx_diff,
    cfx_lint,
    cfx_templates,
    clustering,
    cointegration,
    comparison,
    cost_model,
    cpcv,
    cross_instrument,
    crypto_perp,
    dashboard,
    data,
    data_health,
    data_quality,
    databank_partition,
    databanks,
    diagnostics,
    drawdown_analysis,
    drift,
    engine_watchdog,
    explain,
    exposure,
    fingerprint,
    frac_diff,
    hypothesis,
    indicator_bands,
    info_theory,
    integrity,
    labeling,
    lineage,
    mean_reversion,
    meta,
    montecarlo,
    mt5,
    mt5_ea_template,
    mt5_extra,
    mt5_reports,
    mt5_set_files,
    notify,
    oscillators,
    overfit_diag,
    passthrough,
    performance_attribution,
    pinescript_gen,
    pipeline,
    pipeline_mt5,
    pivots,
    portfolio,
    portfolio_audit,
    portfolio_opt,
    portfolio_risk,
    position_sizing,
    presets,
    projects,
    promotion,
    prompt_safety,
    ratios,
    regime,
    regression,
    reports,
    risk_profiles,
    robustness,
    rolling_metrics,
    schedule,
    sensitivity,
    ship_pipeline,
    signal_quality,
    spa_test,
    sqx_lifecycle,
    state,
    stationary_bootstrap,
    stats_extra,
    strategy_inspect,
    stress_test,
    symbol_intel,
    symbols,
    system_perm,
    tagging,
    tail_risk,
    trade_analysis,
    trade_replay,
    visualize,
    vol_estimators,
    vwap,
    walkforward,
    wf_matrix,
    workflows,
    workspace_doctor,
    workspace_ops,
    workspace_search,
)
from sq_mcp.tools import monitor as monitor_tools
from sq_mcp.tools.monitor import _MANAGER  # noqa: F401  (referenced for shutdown)

# stderr-only logging so we don't corrupt stdio JSON-RPC
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("sq_mcp")


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[EngineClient]:
    """Boot SQ X engine once, share the EngineClient with every tool call."""
    config = detect_config()
    log.info("SQ X home: %s (valid=%s)", config.sqx_home, config.is_valid)
    if not config.is_valid:
        log.warning(
            "sqcli not found — engine-backed tools will fail. "
            "File-only analysis tools (analyze_mq5_file, sqx_inspect, inspect_cfx) still work."
        )
        # We still yield an EngineClient so file-parser tools can use config paths,
        # but engine.start() will be deferred and may raise.
        yield EngineClient(config)
        return

    engine = EngineClient(config)
    try:
        mode = await engine.attach_or_start()
        log.info("SQ X engine %s on %s", mode, config.http_url)
        yield engine
    finally:
        # tear down monitor sessions before engine
        from sq_mcp.tools import monitor as _m
        if _m._MANAGER is not None:
            await _m._MANAGER.stop_all()
        await engine.stop()


mcp = FastMCP(
    "strategyquant",
    instructions=(
        "Drive a local StrategyQuant X installation: list/run/stop projects, "
        "manage databanks, import historical data, monitor builds in real time, "
        "and statically analyze .mq5 / .sqx / .cfx files for risk and configuration issues."
    ),
    lifespan=_lifespan,
)


# Register all tool groups
projects.register(mcp)
databanks.register(mcp)
data.register(mcp)
symbols.register(mcp)
analysis.register(mcp)
monitor_tools.register(mcp)
robustness.register(mcp)
mt5.register(mcp)
diagnostics.register(mcp)
audit.register(mcp)
portfolio.register(mcp)
cfx_config.register(mcp)
regression.register(mcp)
comparison.register(mcp)
pipeline.register(mcp)
integrity.register(mcp)
cfx_advanced.register(mcp)
mt5_extra.register(mcp)
strategy_inspect.register(mcp)
portfolio_risk.register(mcp)
batch.register(mcp)
data_quality.register(mcp)
meta.register(mcp)
analytics.register(mcp)
pipeline_mt5.register(mcp)
backup.register(mcp)
reports.register(mcp)
cfx_diff.register(mcp)
visualize.register(mcp)
cfx_lint.register(mcp)
trade_analysis.register(mcp)
presets.register(mcp)
state.register(mcp)
portfolio_audit.register(mcp)
cfx_templates.register(mcp)
databank_partition.register(mcp)
advisor.register(mcp)
mt5_reports.register(mcp)
passthrough.register(mcp)
engine_watchdog.register(mcp)
workspace_doctor.register(mcp)
workspace_search.register(mcp)
sqx_lifecycle.register(mcp)
explain.register(mcp)
position_sizing.register(mcp)
montecarlo.register(mcp)
clustering.register(mcp)
walkforward.register(mcp)
symbol_intel.register(mcp)
notify.register(mcp)
risk_profiles.register(mcp)
mt5_ea_template.register(mcp)
performance_attribution.register(mcp)
tagging.register(mcp)
drift.register(mcp)
brittle.register(mcp)
cost_model.register(mcp)
schedule.register(mcp)
annual_report.register(mcp)
ratios.register(mcp)
calendar_effects.register(mcp)
lineage.register(mcp)
sensitivity.register(mcp)
alerts.register(mcp)
stress_test.register(mcp)
promotion.register(mcp)
benchmark.register(mcp)
hypothesis.register(mcp)
ship_pipeline.register(mcp)
cross_instrument.register(mcp)
batch_audit.register(mcp)
dashboard.register(mcp)
workflows.register(mcp)
tail_risk.register(mcp)
overfit_diag.register(mcp)
regime.register(mcp)
stats_extra.register(mcp)
fingerprint.register(mcp)
data_health.register(mcp)
portfolio_opt.register(mcp)
trade_replay.register(mcp)
workspace_ops.register(mcp)
pinescript_gen.register(mcp)
mt5_set_files.register(mcp)
labeling.register(mcp)
frac_diff.register(mcp)
cpcv.register(mcp)
crypto_perp.register(mcp)
stationary_bootstrap.register(mcp)
wf_matrix.register(mcp)
spa_test.register(mcp)
system_perm.register(mcp)
vol_estimators.register(mcp)
bar_construction.register(mcp)
mean_reversion.register(mcp)
info_theory.register(mcp)
pivots.register(mcp)
vwap.register(mcp)
prompt_safety.register(mcp)
bar_resampler.register(mcp)
rolling_metrics.register(mcp)
drawdown_analysis.register(mcp)
cointegration.register(mcp)
exposure.register(mcp)
signal_quality.register(mcp)
indicator_bands.register(mcp)
oscillators.register(mcp)


def main() -> None:
    """Entry point used by the `sq-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
