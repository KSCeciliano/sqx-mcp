import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
TOOLS = [
    ('databank_top_bottom', {'project': 'Retester', 'databank': 'phase2_samples', 'metric': 'fitness_oos', 'top_n': 2, 'bottom_n': 2}),
    ('databank_metric_correlation', {'project': 'Retester', 'databank': 'phase2_samples', 'x_metric': 'fitness_oos', 'y_metric': 'profit_to_dd_ratio'}),
    ('databank_metric_histogram', {'project': 'Retester', 'databank': 'phase2_samples', 'metric': 'fitness_oos', 'bins': 5}),
    ('databank_partition', {'project': 'Retester', 'databank': 'phase2_samples', 'metric': 'fitness_oos', 'thresholds': [0.45, 0.55]}),
    ('batch_audit_databank', {'project': 'Retester', 'databank': 'phase2_samples', 'risk_profile': 'balanced', 'top_n': 3}),
    ('databank_audit_summary', {'project': 'Retester', 'databank': 'phase2_samples', 'risk_profile': 'balanced', 'top_n': 3}),
    ('walkforward_databank_summary', {'project': 'Retester', 'databank': 'phase2_samples'}),
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
