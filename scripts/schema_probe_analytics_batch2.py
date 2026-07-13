import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TARGETS={
 'benchmark_compare_curves','benchmark_excess_return_series','benchmark_outperformance_periods',
 'cointegration_spread_series','cointegration_zscore','hypothesis_paired_t_test','hypothesis_unpaired_t_test','hypothesis_wilcoxon_signed_rank',
 'montecarlo_drawdown_distribution','montecarlo_equity_paths','montecarlo_probability_of_drawdown',
 'overfit_deflated_sharpe','overfit_min_track_record_length','overfit_pbo_from_oos_pairs',
 'portfolio_ab_test','portfolio_audit','portfolio_capital_allocation','portfolio_combined_equity','portfolio_combined_equity_explicit','portfolio_concentration','portfolio_contribution','portfolio_deck_markdown','portfolio_dedupe','portfolio_diversity_score','portfolio_export_csv','portfolio_review_bundle',
 'tail_risk_gain_to_pain','walkforward_consistency_score','walkforward_overfit_flags','stress_apply_random_skip','stress_apply_skip_best','stress_apply_slippage','stress_worst_case_dd'
}
async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); tools=await s.list_tools()
            for t in tools.tools:
                if t.name in TARGETS:
                    print(f'=== {t.name}')
                    print(json.dumps(t.inputSchema, ensure_ascii=False, indent=2)[:10000])
asyncio.run(main())
