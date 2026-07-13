import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession
CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS=[
 ('cfx_lint', {'project':'Builder'}),
 ('cfx_recommend_for_symbol', {'symbol':'GBPUSD_M1_dukas'}),
 ('cfx_recommend_settings', {'goal':'robustness','asset_class':'forex'}),
 ('cfx_compare_against_recommendation', {'project':'Builder','goal':'robustness','asset_class':'forex'}),
 ('cfx_configure_walkforward', {'project':'Retester','enable':True,'wf_type':'anchored','period_oos':6,'period_is':24,'target':'first_retest'}),
 ('cfx_configure_robustness', {'project':'Retester','enable': {'MonteCarloRetest': True, 'WalkForwardOptimization': True}, 'target':'first_retest'}),
 ('cfx_inspect_robustness', {'project':'Retester'}),
 ('cfx_template_capture', {'project':'Builder','template_name':'catalog_builder_template'}),
 ('cfx_template_list', {}),
 ('cfx_template_apply', {'project':'Builder','template_name':'catalog_builder_template'}),
 ('cfx_template_delete', {'template_name':'catalog_builder_template'}),
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
