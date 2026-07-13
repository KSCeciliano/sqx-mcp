import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS = [
    ('cfx_set_fitness_criterion', {'project': 'Builder', 'criterion': 'Sharpe'}),
    ('cfx_set_money_management', {'project': 'Builder', 'method': 'FixedSize', 'params': {'Size': '0.2'}}),
    ('cfx_set_trade_caps', {'project': 'Builder', 'max_long_trades': 1, 'max_short_trades': 1}),
    ('cfx_set_sl_pt_range', {'project': 'Builder', 'min_sl': 50, 'max_sl': 500, 'min_pt': 50, 'max_pt': 1000}),
    ('cfx_set_genetic_options', {'project': 'Builder', 'population_size': 50, 'generations': 10}),
    ('cfx_set_instrument', {'project': 'Builder', 'symbol': 'GBPUSD_M1_dukas', 'instrument': 'GBPUSD'}),
    ('cfx_set_setup_attrs', {'project': 'Builder', 'task_index': 1, 'attrs': {'spread': '3'}}),
    ('cfx_toggle_building_blocks', {'project': 'Builder', 'blocks': ['IndicatorBlocks'], 'enabled': True}),
    ('cfx_active_blocks', {'project': 'Builder'}),
    ('cfx_list_building_blocks', {'project': 'Builder'}),
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
