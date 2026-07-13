import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS=[
 ('alert_workspace_defaults', {}),
 ('alert_rule_evaluate', {
   'rule': {'name':'stalled_build','metric_key':'status','op':'==','threshold':'stalled','severity':'high','message':'build appears stalled'},
   'payload': {'status':'stalled'}
 }),
 ('alert_rules_batch', {
   'rules': [
     {'name':'weak_oos','metric_key':'oos_is_ratio','op':'<','threshold':0.3,'severity':'high','message':'overfit'},
     {'name':'low_trades','metric_key':'trades','op':'<','threshold':100,'severity':'medium','message':'too few trades'}
   ],
   'payload': {'oos_is_ratio':0.25,'trades':80}
 }),
 ('drift_compare_average_trade', {'backtest_mean': 100.0, 'backtest_std': 20.0, 'live_trades': [80, 70, 75, 90, 85, 88, 92, 77, 81, 79]}),
 ('drift_compare_drawdown', {'backtest_max_dd_pct': 1.2, 'live_max_dd_pct': 2.5}),
 ('drift_compare_win_rate', {'backtest_win_rate': 0.57, 'live_wins': 47, 'live_losses': 53}),
 ('drift_score_overall', {'backtest_win_rate':0.57,'backtest_mean_trade':100.0,'backtest_std_trade':20.0,'backtest_max_dd_pct':1.2,'live_trades':[80,70,75,90,85,88,92,77,81,79],'live_max_dd_pct':2.5}),
 ('schedule_for_strategy', {'strategy_built_at':'2026-07-01T00:00:00Z','data_updated_at':'2026-07-13T00:00:00Z','live_days_since_deploy':3,'drift_verdict':'green'}),
 ('schedule_for_workspace', {'items':[{'strategy_built_at':'2026-07-01T00:00:00Z','data_updated_at':'2026-07-13T00:00:00Z','live_days_since_deploy':3,'drift_verdict':'green'},{'strategy_built_at':'2026-06-01T00:00:00Z','data_updated_at':'2026-07-10T00:00:00Z','live_days_since_deploy':20,'drift_verdict':'yellow'}]}),
 ('schedule_quarterly_calendar', {'portfolio_size': 12, 'asset_class':'forex'}),
 ('monitor_default_rules', {}),
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
