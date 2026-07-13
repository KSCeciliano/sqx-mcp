import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS=[
 ('portfolio_equal_weight', {'returns': [[0.01,-0.02,0.03],[0.02,-0.01,0.01]]}),
 ('portfolio_inverse_volatility', {'returns': [[0.01,-0.02,0.03],[0.02,-0.01,0.01]]}),
 ('portfolio_min_variance', {'returns': [[0.01,-0.02,0.03],[0.02,-0.01,0.01]]}),
 ('portfolio_risk_parity', {'returns': [[0.01,-0.02,0.03],[0.02,-0.01,0.01]]}),
 ('portfolio_diversification_ratio', {'weights':[0.5,0.5], 'vols':[0.2,0.15], 'corr': [[1.0,0.3],[0.3,1.0]]}),
 ('benchmark_alpha_beta', {'strategy_returns':[0.01,-0.02,0.03], 'benchmark_returns':[0.005,-0.01,0.02]}),
 ('cointegration_engle_granger', {'x':[1,2,3,4,5,6,7,8,9,10], 'y':[2,4,6,8,10,12,14,16,18,20]}),
 ('hypothesis_sign_test', {'samples':[1,-1,1,1,-1,1,1,-1,1,1]}),
 ('stress_combined_scenarios', {'returns':[0.01,-0.02,0.03,-0.01,0.02], 'slippage_bps':10, 'skip_every_n':4}),
 ('regime_classify_trend', {'prices':[100,101,102,103,104,105,106,107,108,109]}),
 ('stats_summary', {'values':[1,2,3,4,5,6,7,8,9,10]}),
 ('ratios_omega', {'returns':[0.01,-0.02,0.03,-0.01,0.02], 'threshold':0.0}),
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
