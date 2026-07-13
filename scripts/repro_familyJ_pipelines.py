import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS = [
    ('promotion_required_gates', {'risk_profile': 'balanced'}),
    ('promotion_explain_failure', {'codes': ['OVERFIT_OOS_DEGRADATION']}),
    ('promotion_evaluate', {'sqx_path': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.101.sqx', 'risk_profile': 'balanced'}),
    ('workflow_compare_two_strategies', {
        'a': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.101.sqx',
        'b': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx'
    }),
    ('workflow_data_health_check', {}),
    ('workflow_morning_briefing', {}),
    ('workflow_promote_strategy', {'sqx_path': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx', 'risk_profile': 'balanced', 'dry_run': True}),
    ('ship_pipeline_dry_run', {'sqx_path': r'C:\SQX_141\SQX_141_Crack\user\projects\Retester\databanks\Results\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx'}),
    ('ship_pipeline_status_summary', {}),
]

async def call(session, name, args):
    print(f'\n=== {name} {json.dumps(args, ensure_ascii=False)}')
    try:
        res = await session.call_tool(name, {'args': args})
        txt='\n'.join(getattr(c,'text',str(c)) for c in getattr(res,'content',[]))
        print(txt[:14000])
    except Exception as e:
        print('ERROR', repr(e))

async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); await s.list_tools()
            for name,args in TOOLS:
                await call(s,name,args)

asyncio.run(main())
