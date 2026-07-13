import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS=[
 ('databank_partition', {'project':'Retester','databank':'phase2_samples','metric':'fitness_oos','threshold':0.5}),
 ('regime_recommend_filter', {'regime_pnl': {'up': {'mean_pnl': 0.008, 'win_rate': 0.62, 'n': 20}, 'down': {'mean_pnl': -0.003, 'win_rate': 0.40, 'n': 20}, 'flat': {'mean_pnl': 0.001, 'win_rate': 0.51, 'n': 20}}}),
 ('cfx_recommend_settings', {'goal':'balanced','asset_class':'forex'}),
 ('cfx_compare_against_recommendation', {'project':'Builder','goal':'balanced','asset_class':'forex'}),
 ('cfx_recommend_settings', {'goal':'risk_min','asset_class':'forex'}),
 ('cfx_recommend_settings', {'goal':'yield','asset_class':'forex'}),
 ('cfx_recommend_settings', {'goal':'smoke','asset_class':'forex'}),
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
