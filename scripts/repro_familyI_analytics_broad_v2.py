import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
RET_A=[0.01,-0.02,0.03,-0.01,0.02,0.01,-0.01,0.02,-0.02,0.01]
RET_B=[0.02,-0.01,0.01,0.00,0.03,0.01,-0.02,0.01,-0.01,0.02]
LONG_X=list(range(1,31))
LONG_Y=[v*2 for v in LONG_X]
TOOLS=[
 ('portfolio_equal_weight', {'returns_by_strategy': {'A': RET_A, 'B': RET_B}}),
 ('portfolio_inverse_volatility', {'returns_by_strategy': {'A': RET_A, 'B': RET_B}}),
 ('portfolio_min_variance', {'returns_by_strategy': {'A': RET_A, 'B': RET_B}}),
 ('portfolio_risk_parity', {'returns_by_strategy': {'A': RET_A, 'B': RET_B}}),
 ('portfolio_diversification_ratio', {'weights': {'A':0.5,'B':0.5}, 'returns_by_strategy': {'A': RET_A, 'B': RET_B}}),
 ('benchmark_alpha_beta', {'strategy_returns': RET_A, 'benchmark_returns': RET_B}),
 ('cointegration_engle_granger', {'x': LONG_X, 'y': LONG_Y}),
 ('hypothesis_sign_test', {'strategy_a': RET_A, 'strategy_b': RET_B}),
 ('stress_combined_scenarios', {'trades': RET_A}),
 ('regime_classify_trend', {'prices': [100+i for i in range(20)]}),
 ('stats_summary', {'returns': RET_A}),
 ('ratios_omega', {'returns': RET_A, 'threshold': 0.0}),
]
async def call(session,name,args):
    print(f'\n=== {name} {json.dumps(args,ensure_ascii=False)}')
    res=await session.call_tool(name, {'args': args})
    txt='\n'.join(getattr(c,'text',str(c)) for c in getattr(res,'content',[]))
    print(txt[:14000])
async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); await s.list_tools()
            for name,args in TOOLS:
                await call(s,name,args)
asyncio.run(main())
