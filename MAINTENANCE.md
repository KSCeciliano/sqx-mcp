# Maintenance playbook

Things to do before this codebase grows past ~50k LOC. The five items below come from a senior-eng review of the codebase as of v0.4.1 (381 tools, 1301 tests, ~29k LOC).

## 1. Module reorganization (low risk, high cognitive payoff)

**Problem:** `src/sq_mcp/tools/` has 76+ flat modules. Finding the right file when adding a feature, or knowing which existing module to extend, requires grepping the directory.

**Plan:** introduce category subpackages without breaking imports:

```
src/sq_mcp/tools/
├── quant/           # pure-math: ratios, tail_risk, overfit_diag, stats_extra,
│                    # regime, montecarlo, walkforward, portfolio_opt, fingerprint
├── sqx/             # SQ X HTTP-API driven: projects, databanks, data, symbols,
│                    # cfx_*, robustness, monitor, engine_watchdog, sqx_lifecycle
├── mt5/             # MetaTrader 5: mt5, mt5_extra, mt5_reports, mt5_ea_template,
│                    # mt5_set_files, pipeline_mt5, pinescript_gen
├── governance/      # promotion, tagging, lineage, alerts, drift, brittle, audit,
│                    # batch_audit, portfolio_audit, integrity, schedule
├── reporting/       # analytics, dashboard, reports, visualize, annual_report,
│                    # performance_attribution, calendar_effects, comparison,
│                    # benchmark, hypothesis
├── ops/             # workflows, ship_pipeline, workspace_doctor, workspace_ops,
│                    # workspace_search, backup, presets, advisor, explain,
│                    # passthrough, state, notify, meta, diagnostics
└── _shared/         # _common.py, _numerics.py, _validation imports
```

**Migration steps:**

1. Create subpackage dirs with empty `__init__.py` re-exporting the flat module names. Example:
   ```python
   # tools/quant/__init__.py
   from sq_mcp.tools import ratios, tail_risk, overfit_diag, ...
   __all__ = ["ratios", "tail_risk", "overfit_diag", ...]
   ```
2. Move one module at a time: physically move file under `quant/`, add a stub at the old path that re-exports for one minor version, then drop the stub on the next.
3. Tests do not move — they keep referencing helpers by absolute path (`from sq_mcp.tools.ratios import _sharpe`). After the file move, update the import line; tests don't care about the subpackage.

**Don't do all at once.** Do `quant/` first (it's the most internally consistent), measure friction over a few iterations, then move on.

## 2. Type checking with mypy

**Decision:** start lax, tighten module-by-module.

Add to `pyproject.toml`:
```toml
[tool.mypy]
python_version = "3.10"
ignore_missing_imports = true       # mcp, httpx, lxml types are noisy
warn_unused_ignores = true
warn_redundant_casts = true
disallow_untyped_defs = false       # tighten per-module via overrides
check_untyped_defs = true

[[tool.mypy.overrides]]
module = ["sq_mcp.tools._numerics", "sq_mcp.tools.ratios", "sq_mcp.tools.tail_risk", "sq_mcp.tools.stats_extra", "sq_mcp.tools.overfit_diag"]
disallow_untyped_defs = true        # strict mode for the pure-math leaf modules
```

Run `mypy src/sq_mcp/tools/_numerics.py` etc. in CI. As each module gets cleaned up, add it to the overrides block.

**The high-value catches mypy provides over Pydantic:** private helper signatures, dict-shape returns, `Optional[X]` narrowing inside `if x is None` branches, dead code.

## 3. Profile before optimizing

When a tool feels slow, do not guess. Use `py-spy`:

```bash
pip install py-spy
py-spy record -o profile.svg -- python scripts/profile_tool.py <tool_name> <args.json>
```

A starter script lives at `scripts/profile_tool.py`. The 80/20 of profiling pure-Python math:

- Sorting once and indexing N times beats sorting N times. Already true in `_percentile`.
- Generators beat list comprehensions when you don't need to keep the result. Already true in `_metrics`.
- Avoid Python-level loops over per-element operations on large arrays — but our `min_length`/`max_length` Pydantic bounds cap arrays at 100k-500k, which is fine in pure Python.
- The slowest math tool in the whole codebase is `montecarlo._run_simulation` at ~10ms for n_paths=1000, n_steps=200. That is not worth optimizing.

The slowest path overall is the SQ X HTTP roundtrip — seconds per call. No Python optimization touches that.

## 4. PyPy as a drop-in

PyPy 3.10 is API-compatible with CPython 3.10 and runs pure-Python loops 4-10x faster. **Zero code changes.** Tradeoffs: lxml works but slower (C extension boundary); httpx works fine; pydantic v2 is slower under PyPy because of its Rust core.

Try it:
```bash
pypy3 -m venv .venv-pypy
source .venv-pypy/bin/activate
pip install -e .[dev]
pytest tests/ --ignore=tests/test_engine.py -q
```

If it works and speeds up the suite, use it for local development and CI. **Do not** ship as the default for end users; their environment is CPython.

## 5. PyO3 surgical extensions

When and only when profiling proves a specific helper matters:

1. Build a Rust crate `sq_mcp_rs/` with `pyo3 = "0.21"`.
2. Export exactly one function as `#[pyfunction]`.
3. Add `maturin develop` to local build flow.
4. Import in the relevant Python module: `from sq_mcp_rs import covariance_matrix`.
5. Fall back to pure-Python implementation if the import fails (CI without Rust toolchain).

**Most likely candidates if/when needed:**
- `portfolio_opt._covariance_matrix` (O(N²·T))
- `montecarlo._run_simulation` (N_paths × N_steps random sampling)
- `clustering._lloyd_kmeans` (iterative)

Each is ~50-100 lines of Rust. **Do not do this preemptively.** Pure-Python is the right default until a profile says otherwise.

## Tripwires for revisiting the language decision

Rewrite to Go or Rust only when one of these is measurably true:

- Multi-tenant: serving >1 trader concurrently. Today it's single-user on localhost.
- A user-perceived tool call exceeds ~500ms in pure Python (not in sqcli).
- Ship-as-single-binary requirement (try PyInstaller first).
- Codebase exceeds ~100k LOC and the friction is from Python itself, not from organization.

Until then: keep adding capabilities in Python, keep `_numerics` discipline at the boundary, keep tests fast.
