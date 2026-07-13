import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
PROMOTION_INPUTS = {'trades': 375,'fitness_oos': 0.561864,'oos_is_ratio': 0.629,'drawdown_pct': 0.7156,'profit_factor': 1.8,'risk_profile': 'balanced','audit_critical_count': 0}
TOOLS=[
 ('pipeline_export_to_mt5', {'project':'Retester','databank':'phase2_samples','n':2,'pack_name':'HermesPipelinePack','deploy':False}),
 ('ship_pipeline_run', {'strategy_name':'NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166','strategy_key':'ndx_m5_up_60_st_136_mt5strategy_1_10_166','promotion_inputs':PROMOTION_INPUTS,'dry_run':False,'tags_if_approved':['catalog-validation']}),
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
