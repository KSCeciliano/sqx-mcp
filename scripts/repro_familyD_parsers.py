import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS = [
    ('project_inspect_cfx', {'name': 'Builder'}),
    ('inspect_cfx', {'path': r'C:\SQX_144_Full\tmp_hermes_save\custom projects\NQ CFD H1 D1 MULTI-TIMEFRAME  - Dukascopy.cfx'}),
    ('inspect_cfx', {'path': r'F:\AlgoTrading\Herramientas\Plantillas para Builder\Build-NDX-M5(60_)Original.cfx'}),
    ('inspect_cfx', {'path': r'F:\AlgoTrading\Herramientas\Plantillas para Retest\RetestQVA141 Original Retest 2.cfx'}),
    ('cfx_compare_paths', {
        'cfx_a': r'F:\AlgoTrading\Herramientas\Plantillas para Builder\Build-NDX-M5(60_)Original.cfx',
        'cfx_b': r'C:\SQX_144_Full\tmp_hermes_save\BUILDER_TEST_HERMES.cfx'
    }),
    ('cfx_archetype', {'cfx_path': r'C:\SQX_144_Full\tmp_hermes_save\custom projects\NQ CFD H1 D1 MULTI-TIMEFRAME  - Dukascopy.cfx'}),
    ('sqx_inspect', {'path': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.101.sqx'}),
]

async def call(session, name, args):
    print(f'\n=== {name} {json.dumps(args, ensure_ascii=False)}')
    try:
        res = await session.call_tool(name, {'args': args})
        txt = '\n'.join(getattr(c, 'text', str(c)) for c in getattr(res, 'content', []))
        print(txt[:14000])
    except Exception as e:
        print('ERROR', repr(e))

async def main():
    async with stdio_client(StdioServerParameters(command=CMD, args=ARGS, env=os.environ.copy())) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize(); await s.list_tools()
            for name, args in TOOLS:
                await call(s, name, args)

asyncio.run(main())
