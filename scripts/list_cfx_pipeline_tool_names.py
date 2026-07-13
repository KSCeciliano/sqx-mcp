import asyncio, os
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']

async def main():
    async with stdio_client(StdioServerParameters(command=CMD, args=ARGS, env=os.environ.copy())) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = sorted(t.name for t in tools.tools if t.name.startswith('cfx_') or 'pipeline' in t.name or 'workflow' in t.name or 'promotion' in t.name or t.name.startswith('ship_'))
            for n in names:
                print(n)

asyncio.run(main())
