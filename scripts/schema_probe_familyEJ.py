import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TARGETS = {
    'cfx_set_fitness_criterion','cfx_set_money_management','cfx_toggle_building_blocks',
    'promotion_required_gates','promotion_explain_failure','promotion_evaluate',
    'workflow_compare_two_strategies','workflow_data_health_check','workflow_morning_briefing',
    'workflow_promote_strategy','ship_pipeline_dry_run','ship_pipeline_status_summary'
}

async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            tools = await s.list_tools()
            for t in tools.tools:
                if t.name in TARGETS:
                    print(f'=== {t.name}')
                    print(json.dumps(t.inputSchema, ensure_ascii=False, indent=2)[:12000])

asyncio.run(main())
