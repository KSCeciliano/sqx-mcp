import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS=[
 ('mt5_ea_generate_basic', {'strategy_name':'HermesBasicEA','symbol':'GBPUSD','timeframe':'H1','entry_rule':'buy when fast MA crosses above slow MA','exit_rule':'close when fast MA crosses below slow MA'}),
 ('mt5_ea_generate_with_trailing', {'strategy_name':'HermesTrailEA','symbol':'GBPUSD','timeframe':'H1','entry_rule':'buy when RSI crosses above 50','exit_rule':'close when RSI crosses below 50','trailing_stop_points':200}),
 ('mt5_ea_inject_risk_guardrails', {'source_code':'input int MagicNumber = 12345;\nvoid OnTick(){}','max_daily_loss_pct':2.0,'max_spread_points':30}),
 ('mt5_set_render', {'inputs': {'Lots':'0.10','StopLoss':'250','TakeProfit':'500'}}),
 ('mt5_set_parse', {'content':'Lots=0.10\nStopLoss=250\nTakeProfit=500\n'}),
 ('mt5_set_merge', {'base': {'Lots':'0.10','StopLoss':'250'}, 'override': {'TakeProfit':'500'}}),
 ('mt5_set_diff', {'old': {'Lots':'0.10','StopLoss':'250'}, 'new': {'Lots':'0.10','StopLoss':'300','TakeProfit':'500'}}),
 ('mt5_set_apply_profile', {'base': {'Lots':'0.10','StopLoss':'250'}, 'profile': 'conservative'}),
 ('mt5_parse_backtest_report', {'path': r'C:\SQX_144_Full\tmp_hermes_save\nonexistent_report.htm'}),
 ('mt5_strategy_pack', {'sqx_path': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.101.sqx', 'ea_name':'HermesPackedEA'}),
 ('mt5_compare_with_sqx', {'sqx_path': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.101.sqx', 'mt5_metrics': {'net_profit':82621.39,'drawdown_pct':1.0009,'trades':431}}),
 ('mt5_log_tail', {}),
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
