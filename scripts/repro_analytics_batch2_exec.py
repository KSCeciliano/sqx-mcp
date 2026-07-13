import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
RET=[0.01,-0.02,0.03,-0.01,0.02,0.01,-0.01,0.02,-0.02,0.01,0.02,-0.01,0.01,0.0,0.03,0.01,-0.02,0.01,-0.01,0.02]
RET2=[0.02,-0.01,0.01,0.0,0.03,0.01,-0.02,0.01,-0.01,0.02,0.01,-0.02,0.02,-0.01,0.01,0.02,-0.01,0.03,-0.02,0.01]
CURVE=[100,101,99,102,103,101,104,106,105,107,108,110]
ITEMS=[
 {'sqx_path': r'C:\SQX_144_Full\user\projects\Retester\databanks\phase2_samples\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx', 'weight': 0.5},
 {'sqx_path': r'C:\SQX_144_Full\user\projects\Retester\databanks\phase2_samples\NDX_M5_UP_60_ST_136_MT5Strategy 1.13.130.sqx', 'weight': 0.5},
]
TOOLS=[
 ('benchmark_compare_curves', {'strategy_curve': CURVE, 'benchmark_curve': [100,100.5,101,101.5,102,102.5,103,103.5,104,104.5,105,105.5]}),
 ('benchmark_excess_return_series', {'strategy_returns': RET, 'benchmark_returns': RET2}),
 ('benchmark_outperformance_periods', {'strategy_returns': RET, 'benchmark_returns': RET2}),
 ('hypothesis_paired_t_test', {'strategy_a': RET, 'strategy_b': RET2}),
 ('hypothesis_unpaired_t_test', {'strategy_a': RET, 'strategy_b': RET2}),
 ('hypothesis_wilcoxon_signed_rank', {'strategy_a': RET, 'strategy_b': RET2}),
 ('tail_risk_gain_to_pain', {'returns': RET}),
 ('overfit_deflated_sharpe', {'sharpe_observed': 1.6, 'n_trials': 50, 'n_observations': 200, 'skewness': 0.1, 'kurtosis': 3.2}),
 ('overfit_min_track_record_length', {'sharpe_observed': 1.6, 'benchmark_sharpe': 0.0, 'skewness': 0.1, 'kurtosis': 3.2}),
 ('overfit_pbo_from_oos_pairs', {'pairs': [{'is_sharpe':1.4,'oos_sharpe':0.8},{'is_sharpe':1.1,'oos_sharpe':0.7},{'is_sharpe':1.3,'oos_sharpe':0.6},{'is_sharpe':1.0,'oos_sharpe':0.5}]}),
 ('stress_apply_slippage', {'trades': RET, 'slip_pct': 2.0}),
 ('stress_apply_skip_best', {'trades': RET, 'n_best_to_remove': 2}),
 ('stress_apply_random_skip', {'trades': RET, 'skip_fraction': 0.2, 'seed': 42}),
 ('stress_worst_case_dd', {'trades': RET, 'streak_length': 5}),
 ('montecarlo_probability_of_drawdown', {'returns': RET, 'drawdown_threshold_pct': 10.0, 'n_paths': 200, 'seed': 42}),
 ('portfolio_combined_equity_explicit', {'items': ITEMS, 'curve': 'full'}),
 ('portfolio_contribution', {'items': ITEMS, 'curve': 'full'}),
]
async def call(session,name,args):
    print(f'\n=== {name} {json.dumps(args, ensure_ascii=False)}')
    res=await session.call_tool(name, {'args': args})
    txt='\n'.join(getattr(c,'text',str(c)) for c in getattr(res,'content',[]))
    print(txt[:16000])
async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); await s.list_tools()
            for name,args in TOOLS:
                await call(s,name,args)
asyncio.run(main())
