import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
OUTDIR='/home/kev/sqx-mcp-fork/.hermes/tmp'
os.makedirs(OUTDIR, exist_ok=True)
ITEMS=[
 {'sqx_path': r'C:\SQX_144_Full\user\projects\Retester\databanks\phase2_samples\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx', 'weight': 0.5},
 {'sqx_path': r'C:\SQX_144_Full\user\projects\Retester\databanks\phase2_samples\NDX_M5_UP_60_ST_136_MT5Strategy 1.13.130.sqx', 'weight': 0.5},
]
TOOLS=[
 ('portfolio_ab_test', {'project_a':'Retester','databank_a':'phase2_samples','project_b':'Retester','databank_b':'phase2_samples','min_trades':30}),
 ('portfolio_audit', {'project':'Retester','databank':'phase2_samples','min_trades':30}),
 ('portfolio_capital_allocation', {'project':'Retester','databank':'phase2_samples','n':2,'rank_mode':'defensive','min_trades':30,'fitness_bias':0.5}),
 ('portfolio_combined_equity', {'project':'Retester','databank':'phase2_samples','n':2,'rank_mode':'defensive','min_trades':30,'weight_by':'uniform','curve':'full'}),
 ('portfolio_concentration', {'project':'Retester','databank':'phase2_samples','min_trades':30}),
 ('portfolio_dedupe', {'project':'Retester','databank':'phase2_samples','by':'trades_hash','keep':'best_oos'}),
 ('portfolio_diversity_score', {'project':'Retester','databank':'phase2_samples','min_trades':30}),
 ('portfolio_export_csv', {'project':'Retester','databank':'phase2_samples','output_path': os.path.join(OUTDIR,'phase2_samples_export.csv'),'min_trades':30,'rank_mode':'defensive','top_n':2}),
 ('portfolio_deck_markdown', {'project':'Retester','databank':'phase2_samples','n':2,'rank_mode':'defensive','min_trades':30,'output_path': os.path.join(OUTDIR,'phase2_samples_deck.md')}),
 ('portfolio_review_bundle', {'project':'Retester','databank':'phase2_samples','n':2,'rank_mode':'defensive','min_trades':30,'output_dir': OUTDIR,'label':'catalog'}),
 ('cointegration_spread_series', {'y': list(range(2,42,2)), 'x': list(range(1,21)), 'hedge_ratio': 2.0}),
 ('cointegration_zscore', {'spread': [0,1,-1,2,-2,1,-1,0,1,-1,2,-2,0,1,-1,2,-2,1,0,-1]}),
 ('montecarlo_equity_paths', {'sqx_path': r'C:\SQX_144_Full\user\projects\Retester\databanks\phase2_samples\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx','n_paths':100,'seed':42}),
 ('montecarlo_drawdown_distribution', {'sqx_path': r'C:\SQX_144_Full\user\projects\Retester\databanks\phase2_samples\NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166.sqx','n_paths':100,'seed':42}),
]
async def call(session,name,args):
    print(f'\n=== {name} {json.dumps(args, ensure_ascii=False)}')
    res=await session.call_tool(name, {'args': args})
    txt='\n'.join(getattr(c,'text',str(c)) for c in getattr(res,'content',[]))
    print(txt[:16000])
async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); await s.list_tools()
            for name,args in TOOLS:
                await call(s,name,args)
asyncio.run(main())
