import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TARGETS={
 'alert_rule_evaluate','alert_rules_batch','drift_compare_average_trade','drift_compare_drawdown','drift_compare_win_rate','drift_score_overall',
 'schedule_for_strategy','schedule_for_workspace','schedule_quarterly_calendar',
 'mt5_ea_generate_basic','mt5_ea_generate_with_trailing','mt5_ea_inject_risk_guardrails','mt5_set_render','mt5_set_parse','mt5_set_merge','mt5_set_diff','mt5_set_apply_profile','mt5_parse_backtest_report','mt5_strategy_pack','mt5_compare_with_sqx','mt5_log_tail',
 'portfolio_equal_weight','portfolio_inverse_volatility','portfolio_min_variance','portfolio_risk_parity','portfolio_diversification_ratio','benchmark_alpha_beta','cointegration_engle_granger','hypothesis_sign_test','stress_combined_scenarios','regime_classify_trend','stats_summary','ratios_omega'
}
async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            tools=await s.list_tools()
            for t in tools.tools:
                if t.name in TARGETS:
                    print(f'=== {t.name}')
                    print(json.dumps(t.inputSchema, ensure_ascii=False, indent=2)[:12000])
asyncio.run(main())
