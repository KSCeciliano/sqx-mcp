import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS=[
 ('alert_workspace_defaults', {}),
 ('alert_rule_evaluate', {'rule': {'code':'STALLED_GROWTH','threshold':0,'window_checks':3}, 'context': {'deltas':[0,0,0], 'project_running': True}}),
 ('alert_rules_batch', {'rules': [{'code':'STALLED_GROWTH','threshold':0,'window_checks':3},{'code':'PROJECT_NOT_RUNNING','window_checks':1}], 'context': {'deltas':[0,0,0], 'project_running': False}}),
 ('drift_compare_average_trade', {'baseline':[100,120,90,110], 'current':[80,70,75,90]}),
 ('drift_compare_drawdown', {'baseline':[1.2,1.0,1.4], 'current':[2.5,2.2,2.8]}),
 ('drift_compare_win_rate', {'baseline':[0.55,0.58,0.57], 'current':[0.47,0.49,0.5]}),
 ('drift_score_overall', {'checks':[{'verdict':'green'},{'verdict':'yellow'},{'verdict':'red'}]}),
 ('schedule_for_strategy', {'start_date':'2026-07-14','cadence':'weekly'}),
 ('schedule_for_workspace', {'start_date':'2026-07-14','cadence':'monthly'}),
 ('schedule_quarterly_calendar', {'year':2026}),
 ('monitor_default_rules', {}),
]
async def call(session,name,args):
    print(f'\n=== {name} {json.dumps(args,ensure_ascii=False)}')
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
