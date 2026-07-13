import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TARGETS = {
    'cfx_compare_paths',
    'cfx_archetype',
    'workspace_overview',
    'workspace_disk_usage',
    'workspace_insights',
    'project_pause',
    'project_resume',
    'engine_status',
}

async def main():
    async with stdio_client(StdioServerParameters(command=CMD, args=ARGS, env=os.environ.copy())) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            for t in tools.tools:
                if t.name in TARGETS:
                    print(f'=== {t.name}')
                    print(json.dumps(t.inputSchema, ensure_ascii=False, indent=2)[:12000])

asyncio.run(main())
