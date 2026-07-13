import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS = [
    ('databank_create', {'project': 'Retester', 'name': 'catalog_tmp_db'}),
    ('databank_copy', {'project': 'Retester', 'name': 'phase2_samples', 'destproject': 'Retester', 'destdatabank': 'catalog_tmp_copy'}),
    ('databank_count', {'project': 'Retester', 'name': 'catalog_tmp_copy'}),
    ('databank_move', {'project': 'Retester', 'name': 'catalog_tmp_copy', 'destproject': 'Retester', 'destdatabank': 'catalog_tmp_moved'}),
    ('databank_count', {'project': 'Retester', 'name': 'catalog_tmp_moved'}),
    ('databank_export', {'project': 'Retester', 'name': 'phase2_samples', 'file': r'C:\SQX_144_Full\tmp_hermes_save\catalog_phase2_export.csv'}),
    ('databank_remove', {'project': 'Retester', 'name': 'catalog_tmp_db'}),
    ('databank_remove', {'project': 'Retester', 'name': 'catalog_tmp_moved'}),
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
