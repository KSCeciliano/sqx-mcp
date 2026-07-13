import asyncio, os
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

CMD='cmd.exe'
ARGS=['/c', r'C:\Users\Pke\tools\sqx-mcp-win\run-sqxwin.cmd']
KEYSETS = {
    'monitor': lambda n: 'monitor' in n or 'alert' in n or 'drift' in n or 'schedule' in n,
    'mt5': lambda n: n.startswith('mt5_'),
    'analytics': lambda n: n.startswith('portfolio_') or n.startswith('strategy_') or n.startswith('tail_risk_') or n.startswith('overfit_') or n.startswith('walkforward_') or n.startswith('montecarlo_') or n.startswith('benchmark_') or n.startswith('ratios_') or n.startswith('stats_') or n.startswith('volatility_') or n.startswith('hypothesis_') or n.startswith('stress_') or n.startswith('regime_') or n.startswith('correlation_') or n.startswith('cointegration_') or n.startswith('clustering_')
}

async def main():
    async with stdio_client(StdioServerParameters(command=CMD,args=ARGS,env=os.environ.copy())) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = sorted(t.name for t in tools.tools)
            for label, pred in KEYSETS.items():
                print(f'=== {label.upper()}')
                for n in names:
                    if pred(n):
                        print(n)

asyncio.run(main())
