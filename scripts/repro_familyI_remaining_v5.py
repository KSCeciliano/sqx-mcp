import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
EQ=[100,101,99,102,103,101,104,106,105,107,108,110]
RET=[0.01,-0.02,0.03,-0.01,0.02,0.01,-0.01,0.02,-0.02,0.01,0.02,-0.01,0.01,0.0,0.03,0.01,-0.02,0.01,-0.01,0.02,0.0,0.01,-0.01,0.02,-0.01,0.01,0.0,0.02,-0.02,0.01]
RET_B=RET[::-1]
TOOLS=[
 ('ratios_calmar', {'total_return_pct': 12.5, 'max_drawdown_pct': 3.2, 'years_observed': 1.0}),
 ('ratios_mar', {'total_return_pct': 12.5, 'max_drawdown_pct': 3.2, 'years_observed': 1.0}),
 ('ratios_pain_index', {'equity_curve': EQ}),
 ('ratios_ulcer_index', {'equity_curve': EQ}),
 ('stats_autocorrelation', {'series': RET, 'lag': 1}),
 ('stats_time_weighted_return', {'period_returns_pct': [r*100 for r in RET]}),
 ('regime_pnl_split', {'trade_pnls': RET, 'trade_regimes': ['up']*10 + ['down']*10 + ['flat']*10}),
 ('regime_recommend_filter', {'regime_pnl': {'up': 0.08, 'down': -0.03, 'flat': 0.01}}),
 ('regime_classify_volatility', {'prices': [100 + i*0.5 for i in range(25)]}),
]
async def call(session,name,args):
    print(f'\n=== {name} {json.dumps(args, ensure_ascii=False)}')
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
