import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS = [
    ('cfx_set_fitness_criterion', {'project': 'Builder', 'ranking_type': 'Sharpe'}),
    ('cfx_set_money_management', {'project': 'Builder', 'method_type': 'FixedSize', 'params': {'Size': '0.2'}}),
    ('cfx_toggle_building_blocks', {'project': 'Builder', 'enable': True, 'category': 'signals'}),
    ('cfx_active_blocks', {'project': 'Builder'}),
    ('cfx_list_building_blocks', {'project': 'Builder'}),
    ('cfx_compare_against_recommendation', {'project': 'Builder', 'goal': 'robustness', 'asset_class': 'forex'}),
    ('cfx_recommend_for_symbol', {'symbol': 'GBPUSD_M1_dukas'}),
    ('cfx_recommend_settings', {'goal': 'robustness', 'asset_class': 'forex'}),
]

async def call(session, name, args):
    print(f'\n=== {name} {json.dumps(args, ensure_ascii=False)}')
    try:
        res = await session.call_tool(name, {'args': args})
        txt='\n'.join(getattr(c,'text',str(c)) for c in getattr(res,'content',[]))
        print(txt[:14000])
    except Exception as e:
        print('ERROR', repr(e))

async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); await s.list_tools()
            for name,args in TOOLS:
                await call(s,name,args)

asyncio.run(main())
