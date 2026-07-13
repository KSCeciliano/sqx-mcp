import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
RET=[0.01,-0.02,0.03,-0.01,0.02,0.01,-0.01,0.02,-0.02,0.01,0.02,-0.01,0.01,0.0,0.03,0.01,-0.02,0.01,-0.01,0.02]
SERIES_BY_NAME={
    'A': list(range(1,31)),
    'B': [v*2 for v in range(1,31)],
    'C': [100 + ((-1)**i) * (i%5) for i in range(1,31)],
}
TOOLS=[
 ('cointegration_pair_scan', {'series_by_name': SERIES_BY_NAME, 'top_n': 3}),
 ('cointegration_zscore', {'y': list(range(2,42,2)), 'x': list(range(1,21)), 'hedge_ratio': 2.0, 'window': 10}),
 ('montecarlo_equity_paths', {'sqx_path': r'C:\SQX_144_Full\user\projects\Retester\databanks\phase2_samples\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx','n_paths':100,'seed':42}),
 ('montecarlo_drawdown_distribution', {'sqx_path': r'C:\SQX_144_Full\user\projects\Retester\databanks\phase2_samples\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx','n_paths':100,'seed':42}),
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
