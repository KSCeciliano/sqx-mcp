import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS = [
    ('broker_registry', {}),
    ('data_timezones', {}),
    ('data_workspace_quality_report', {}),
    ('history_disk_inventory', {}),
    ('broker_registry_query', {'query': 'GBPUSD'}),
]

async def call(session, name, args):
    print(f'\n=== {name} {json.dumps(args, ensure_ascii=False)}')
    try:
        res = await session.call_tool(name, {'args': args})
        txt = '\n'.join(getattr(c, 'text', str(c)) for c in getattr(res, 'content', []))
        print(txt[:12000])
    except Exception as e:
        print('ERROR', repr(e))

async def main():
    async with stdio_client(StdioServerParameters(command=CMD, args=ARGS, env=os.environ.copy())) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize(); await s.list_tools()
            for name, args in TOOLS:
                await call(s, name, args)

asyncio.run(main())
