"""Profile a single tool helper under py-spy.

Usage:
    py-spy record -o profile.svg -- python scripts/profile_tool.py montecarlo

Add a new case to ``_RUNNERS`` when you want to profile a specific helper.
The point is to be deterministic and easy to invoke — no JSON parsing,
no argparse — just pick a name and re-run a fixed workload.
"""

from __future__ import annotations

import random
import sys
import time


def _run_montecarlo() -> None:
    from sq_mcp.tools.montecarlo import _run_simulation
    rng = random.Random(0)
    returns = [rng.gauss(0.0, 0.02) for _ in range(500)]
    for _ in range(20):
        _run_simulation(returns, n_paths=2000, n_steps=500, seed=42)


def _run_covariance() -> None:
    from sq_mcp.tools.portfolio_opt import _covariance_matrix
    rng = random.Random(0)
    returns = {f"S{i}": [rng.gauss(0.0, 1.0) for _ in range(1000)] for i in range(20)}
    for _ in range(50):
        _covariance_matrix(returns)


def _run_walkforward() -> None:
    from sq_mcp.tools.walkforward import _aggregate_folds
    folds = [
        {"is_metric": rng_v, "oos_metric": rng_v * 0.85, "fold_index": i}
        for i, rng_v in enumerate([0.5 + i * 0.01 for i in range(40)])
    ]
    for _ in range(100):
        _aggregate_folds(folds)


def _run_brittle() -> None:
    from sq_mcp.tools.brittle import _trade_gini, _trade_pareto
    rng = random.Random(0)
    trades = [rng.gauss(50.0, 100.0) for _ in range(5000)]
    for _ in range(100):
        _trade_gini(trades)
        _trade_pareto(trades, top_share_pct=0.2)


_RUNNERS = {
    "montecarlo": _run_montecarlo,
    "covariance": _run_covariance,
    "walkforward": _run_walkforward,
    "brittle": _run_brittle,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in _RUNNERS:
        print(f"Usage: profile_tool.py <{'|'.join(_RUNNERS)}>")
        return 1
    name = sys.argv[1]
    print(f"Running {name} workload...")
    t0 = time.perf_counter()
    _RUNNERS[name]()
    elapsed = time.perf_counter() - t0
    print(f"Done in {elapsed:.3f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
