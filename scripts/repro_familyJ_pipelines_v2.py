import asyncio, os, json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
PROMOTION_INPUTS = {
    'trades': 375,
    'fitness_oos': 0.561864,
    'oos_is_ratio': 0.629,
    'drawdown_pct': 0.7156,
    'profit_factor': 1.8,
    'risk_profile': 'balanced',
    'audit_critical_count': 0,
}

async def call(session, name, args):
    print(f'\n=== {name} {json.dumps(args, ensure_ascii=False)}')
    res = await session.call_tool(name, {'args': args})
    txt='\n'.join(getattr(c,'text',str(c)) for c in getattr(res,'content',[]))
    print(txt[:14000])
    return txt

async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); await s.list_tools()
            print('\n=== promotion_required_gates {}')
            res = await s.call_tool('promotion_required_gates', {'args': {}})
            txt='\n'.join(getattr(c,'text',str(c)) for c in getattr(res,'content',[]))
            print(txt[:14000])

            eval_txt = await call(s, 'promotion_evaluate', {
                'strategy_name': 'NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166',
                'inputs': PROMOTION_INPUTS,
            })
            try:
                eval_obj = json.loads(eval_txt)
            except Exception:
                eval_obj = {'raw': eval_txt}
            await call(s, 'promotion_explain_failure', {'result': eval_obj})
            await call(s, 'workflow_morning_briefing', {})
            await call(s, 'workflow_data_health_check', {
                'symbol': 'GBPUSD_M1_dukas',
                'timeframe': 'M1',
                'last_bar_epoch': 1730257140,
                'now_epoch': 1730257440,
                'n_bars': 8032276,
                'expected_interval_seconds': 60,
            })
            await call(s, 'workflow_promote_strategy', {
                'strategy_name': 'NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166',
                'strategy_key': 'ndx_m5_up_60_st_136_mt5strategy_1_10_166',
                'promotion_inputs': PROMOTION_INPUTS,
                'dry_run': True,
                'tags_if_approved': ['catalog-validation'],
            })
            await call(s, 'ship_pipeline_dry_run', {
                'strategy_name': 'NDX_M5_UP_60_ST_136_MT5Strategy 1.10.166',
                'strategy_key': 'ndx_m5_up_60_st_136_mt5strategy_1_10_166',
                'promotion_inputs': PROMOTION_INPUTS,
                'dry_run': True,
                'tags_if_approved': ['catalog-validation'],
            })
            await call(s, 'ship_pipeline_status_summary', {'manifest': {'strategy_name': 'demo', 'status': 'dry_run', 'steps': ['promotion', 'packaging']}})

asyncio.run(main())
