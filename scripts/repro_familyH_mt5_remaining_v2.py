import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
SOURCE='input int MagicNumber = 12345;\nvoid OnTick(){}\n'
TOOLS=[
 ('mt5_log_tail', {'log_type':'terminal','lines':20}),
 ('mt5_ea_generate_basic', {'ea_name':'HermesBasicEA','symbol':'GBPUSD','magic_number':1234501}),
 ('mt5_ea_generate_with_trailing', {'ea_name':'HermesTrailEA','symbol':'GBPUSD','magic_number':1234502}),
 ('mt5_ea_inject_risk_guardrails', {'source': SOURCE, 'magic_number': 1234503}),
 ('mt5_set_render', {'sections': {'Inputs': {'Lots':'0.10','StopLoss':'250','TakeProfit':'500'}}}),
 ('mt5_set_parse', {'raw_text':'Lots=0.10\nStopLoss=250\nTakeProfit=500\n'}),
 ('mt5_set_merge', {'base_text':'Lots=0.10\nStopLoss=250\n', 'overlay_text':'TakeProfit=500\n', 'overlay_wins': True}),
 ('mt5_set_diff', {'a_text':'Lots=0.10\nStopLoss=250\n', 'b_text':'Lots=0.10\nStopLoss=300\nTakeProfit=500\n'}),
 ('mt5_set_apply_profile', {'set_text':'Lots=0.10\nStopLoss=250\n', 'profile':'conservative', 'magic_number':1234504}),
 ('mt5_parse_backtest_report', {'report_path': r'C:\SQX_144_Full\tmp_hermes_save\nonexistent_report.htm'}),
 ('mt5_compare_with_sqx', {'report_path': r'C:\SQX_144_Full\tmp_hermes_save\nonexistent_report.htm', 'sqx_path': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.101.sqx'}),
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
