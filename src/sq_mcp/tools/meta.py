"""Self-describing meta tools: tools_catalog + health_check.

These two tools answer "what can this plugin actually do?" and "is everything
wired up correctly?" — both questions the agent should ask first before
attempting a long workflow.

- ``tools_catalog`` — returns every registered tool name + its declared
  description, optionally grouped by category. Cheap, no I/O.
- ``health_check`` — comprehensive environment audit: SQ X install presence,
  sqcli binary, engine reachability (best-effort HTTP probe), data + projects
  dirs, history files, data.db registry, optional MT5 install detection.
"""

from __future__ import annotations

import platform
import shutil
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from sq_mcp.engine import EngineError
from sq_mcp.tools._common import get_engine, safe_error_payload
from sq_mcp.tools.mt5 import _resolve_mt5_install_root
from sq_mcp.tools.symbols import _open_data_registry

# ---- argument schemas ------------------------------------------------------


class ToolsCatalogArgs(BaseModel):
    name_contains: str | None = Field(
        None,
        description="Optional case-insensitive substring filter on tool name.",
    )
    description_contains: str | None = Field(
        None, description="Optional substring filter on the tool description."
    )


class HealthCheckArgs(BaseModel):
    probe_engine_http: bool = Field(
        True,
        description=(
            "If True, attempt a TCP connect to the engine HTTP port. Set False "
            "to skip the network check (saves ~1s if engine is down)."
        ),
    )


class EngineLogExportArgs(BaseModel):
    output_path: str = Field(..., description="Where to write the dumped log tail.")


class ToolRecommendArgs(BaseModel):
    query: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Free-form description of what you want to do (e.g. 'check if data is stale').",
    )
    top_n: int = Field(5, ge=1, le=20, description="How many top matches to return.")


# ---- helpers ---------------------------------------------------------------


def _categorize_tool(name: str) -> str:
    """Map a tool name to a category. Conservative — falls back to 'misc'."""
    # Order matters — more specific prefixes first to avoid false matches.
    table = (
        ("projects_", "projects"),
        ("project_", "projects"),
        ("analyze_mq5", "audit"),
        ("compare_mq5", "audit"),
        ("databank_", "databanks"),
        ("portfolio_", "portfolio"),
        ("strategy_", "strategy"),
        ("sqx_", "strategy"),
        ("trade_csv", "strategy"),
        ("cfx_", "cfx_config"),
        ("preset_", "cfx_config"),
        ("data_", "data"),
        ("history_", "data"),
        ("symbol_", "symbols"),
        ("instrument_", "symbols"),
        ("broker_", "symbols"),
        ("mt5_", "mt5"),
        ("monitor_", "monitor"),
        ("audit", "audit"),
        ("anomaly", "audit"),
        ("inspect_cfx", "cfx_config"),
        ("inspect_", "audit"),
        ("pre_live", "audit"),
        ("explain_", "meta"),
        ("license_", "engine"),
        ("task_progress", "engine"),
        ("engine_", "engine"),
        ("robustness", "robustness"),
        ("walkforward", "robustness"),
        ("regression", "regression"),
        ("snapshot", "regression"),
        ("pipeline", "pipeline"),
        ("workspace_", "meta"),
        ("session_", "meta"),
        ("health_", "meta"),
        ("tools_", "meta"),
        ("tool_recommend", "meta"),
        ("common_workflows", "meta"),
        ("env_path_tools", "meta"),
        ("agent_init", "meta"),
        ("version_info", "meta"),
        ("state_", "state"),
    )
    n = name.lower()
    for needle, cat in table:
        if needle in n:
            return cat
    return "misc"


def _probe_tcp(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _summarize_dir(path: Any) -> dict[str, Any]:
    """Filesystem summary of a directory without recursive scan."""
    try:
        if not path.exists():
            return {"path": str(path), "exists": False}
        if not path.is_dir():
            return {"path": str(path), "exists": True, "is_dir": False}
        entries = list(path.iterdir())
        return {
            "path": str(path),
            "exists": True,
            "is_dir": True,
            "entry_count": len(entries),
            "subdir_count": sum(1 for e in entries if e.is_dir()),
        }
    except OSError as exc:
        return {"path": str(path), "error": str(exc)}


# ---- registration ----------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Self-describing catalog of every tool this MCP server exposes. "
            "Returns name + description + heuristic category for each tool. "
            "Optionally filter by substring match on name or description. "
            "Free / no I/O — use to discover available capabilities."
        )
    )
    async def tools_catalog(args: ToolsCatalogArgs, ctx: Context) -> dict:
        try:
            tools = mcp._tool_manager._tools  # noqa: SLF001 (intentional introspection)
            rows: list[dict[str, Any]] = []
            for name, tool in tools.items():
                desc = (tool.description or "").strip()
                if args.name_contains and args.name_contains.lower() not in name.lower():
                    continue
                if (
                    args.description_contains
                    and args.description_contains.lower() not in desc.lower()
                ):
                    continue
                rows.append(
                    {
                        "name": name,
                        "category": _categorize_tool(name),
                        "description": desc,
                    }
                )
            rows.sort(key=lambda r: (r["category"], r["name"]))
            by_cat: dict[str, int] = {}
            for r in rows:
                by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
            return {
                "ok": True,
                "total_tools": len(tools),
                "matched_tools": len(rows),
                "categories": dict(sorted(by_cat.items())),
                "tools": rows,
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "End-to-end environment health probe. Checks SQ X install, sqcli "
            "binary, engine HTTP port reachability, data.db readability, "
            "history dir presence, projects dir, MT5 install detection. "
            "Returns a 'healthy' boolean and a per-check breakdown. Quick — "
            "no engine RPC calls."
        )
    )
    async def environment_health_check(args: HealthCheckArgs, ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfg = eng.config
            checks: list[dict[str, Any]] = []
            healthy = True

            def _check(name: str, ok: bool, **extra: Any) -> None:
                nonlocal healthy
                if not ok:
                    healthy = False
                checks.append({"name": name, "ok": ok, **extra})

            _check("sqx_home_exists", cfg.sqx_home.exists(), path=str(cfg.sqx_home))
            _check("sqcli_binary_exists", cfg.sqcli.exists(), path=str(cfg.sqcli))
            _check("user_dir_exists", cfg.user_dir.exists(), path=str(cfg.user_dir))
            _check("data_dir_exists", cfg.data_dir.exists(), **_summarize_dir(cfg.data_dir))
            _check(
                "projects_dir_exists",
                cfg.projects_dir.exists(),
                **_summarize_dir(cfg.projects_dir),
            )
            _check(
                "history_dir_exists",
                cfg.history_dir.exists(),
                **_summarize_dir(cfg.history_dir),
            )

            db_path = cfg.data_dir / "data.db"
            con = _open_data_registry(cfg.data_dir)
            db_ok = con is not None
            data_row_count: int | None = None
            if con is not None:
                try:
                    cur = con.execute("SELECT COUNT(*) FROM DATA")
                    data_row_count = int(cur.fetchone()[0])
                finally:
                    con.close()
            _check(
                "data_db_readable",
                db_ok,
                path=str(db_path),
                data_row_count=data_row_count,
            )

            # Engine HTTP port probe (TCP connect only — no HTTP request)
            if args.probe_engine_http:
                parsed = urlparse(cfg.http_url)
                host = parsed.hostname or "localhost"
                port = parsed.port or cfg.http_port
                reachable = _probe_tcp(host, port)
                _check(
                    "engine_http_port_reachable",
                    reachable,
                    host=host,
                    port=port,
                    url=cfg.http_url,
                )

            # MT5 install (optional — failure isn't critical for SQ workflows)
            mt5_root = _resolve_mt5_install_root()
            checks.append(
                {
                    "name": "mt5_install_found",
                    "ok": mt5_root is not None,
                    "path": str(mt5_root) if mt5_root else None,
                    "note": "not required for SQ-only workflows",
                }
            )

            # Python deps probe (lxml, pydantic) — if these import-fail the
            # server would never have started, so this is a sanity belt-and-braces.
            for mod in ("lxml", "pydantic", "mcp"):
                try:
                    __import__(mod)
                    _check(f"dependency_{mod}_importable", True)
                except ImportError as exc:
                    _check(f"dependency_{mod}_importable", False, error=str(exc))

            return {
                "ok": True,
                "healthy": healthy,
                "now": datetime.now(tz=timezone.utc).isoformat(),
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "sqx_home": str(cfg.sqx_home),
                "http_url": cfg.http_url,
                "checks": checks,
                "checks_failed": [c["name"] for c in checks if not c["ok"]],
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Find tools matching a free-form natural-language query. Splits "
            "the query into keywords, scores each tool by how many keywords "
            "appear in its name + description, returns the top-N matches. "
            "Use when you know what you want to do but not which tool to call."
        )
    )
    async def tool_recommend(args: ToolRecommendArgs, ctx: Context) -> dict:  # noqa: ARG001
        try:
            tools = mcp._tool_manager._tools  # noqa: SLF001
            terms = [t.lower() for t in args.query.split() if len(t) >= 2]
            scored: list[tuple[int, str, str]] = []
            for name, tool in tools.items():
                hay = (name + " " + (tool.description or "")).lower()
                score = sum(1 for term in terms if term in hay)
                if score == 0:
                    continue
                scored.append((score, name, (tool.description or "").strip()))
            scored.sort(key=lambda r: (-r[0], r[1]))
            top = scored[: args.top_n]
            return {
                "ok": True,
                "query": args.query,
                "keywords_used": terms,
                "match_count": len(scored),
                "top_matches": [
                    {"name": n, "score": s, "description": d[:200]}
                    for s, n, d in top
                ],
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Plugin version + Python version + key dependency versions. Useful "
            "for support / bug reports. Read-only."
        )
    )
    async def version_info(ctx: Context) -> dict:  # noqa: ARG001
        try:
            from importlib.metadata import PackageNotFoundError, version
            sq_version = "unknown"
            try:
                sq_version = version("sq-mcp")
            except PackageNotFoundError:
                pass
            deps = {}
            for dep in ("mcp", "httpx", "pydantic", "lxml"):
                try:
                    deps[dep] = version(dep)
                except PackageNotFoundError:
                    deps[dep] = "not-installed"
            return {
                "ok": True,
                "sq_mcp_version": sq_version,
                "python_version": platform.python_version(),
                "platform": platform.system(),
                "platform_release": platform.release(),
                "dependencies": deps,
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "List which shell command-line tools are visible on $PATH that this "
            "MCP plugin might depend on. Returns presence + resolved path for "
            "common helpers (curl, unzip, sqlite3, etc.). Read-only."
        )
    )
    async def env_path_tools_check(ctx: Context) -> dict:
        try:
            wanted = [
                "sqcli", "curl", "unzip", "zip", "sqlite3", "java",
                "python3", "pytest", "ruff",
            ]
            rows = [
                {
                    "name": t,
                    "present": shutil.which(t) is not None,
                    "path": shutil.which(t),
                }
                for t in wanted
            ]
            return {
                "ok": True,
                "platform": platform.system(),
                "tools": rows,
                "missing": [r["name"] for r in rows if not r["present"]],
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Dump the engine's bounded log tail to a file path. Useful when "
            "you want to share or grep through more than what fits in a JSON "
            "response. Up to ~200 lines (the in-memory ring buffer size)."
        )
    )
    async def engine_log_export(args: EngineLogExportArgs, ctx: Context) -> dict:
        try:
            from sq_mcp._validation import resolve_safe_path
            eng = get_engine(ctx)
            p = resolve_safe_path(args.output_path, must_exist=False)
            p.parent.mkdir(parents=True, exist_ok=True)
            lines = eng.recent_log
            p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            return {
                "ok": True,
                "output_path": str(p),
                "lines_written": len(lines),
                "bytes": p.stat().st_size,
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Surface the most recent fatal/error/exception lines from the "
            "engine's log tail. Useful right after a failed Builder run or "
            "an HTTP timeout to see what the engine said about it."
        )
    )
    async def engine_recent_errors(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            lines = eng.recent_log
            # Filter for error-shaped lines without over-matching
            err_patterns = ("error", "exception", "fatal", "failed", "trace:")
            matches: list[dict[str, Any]] = []
            for i, line in enumerate(lines):
                lower = line.lower()
                if any(p in lower for p in err_patterns):
                    matches.append({"index": i, "line": line})
            return {
                "ok": True,
                "log_path": str(eng.config.log_path) if eng.config.log_path else None,
                "total_recent_lines": len(lines),
                "error_match_count": len(matches),
                "errors": matches[-50:],  # last 50
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Single-call orientation for an agent starting a new session. "
            "Bundles: environment health probe, workspace overview, recent "
            "state-store entries, common-workflow recipes, and a tool count "
            "by category. Use this as the first call so the agent knows what's "
            "available and what's been remembered from past sessions."
        )
    )
    async def session_bootstrap(ctx: Context) -> dict:
        try:
            eng = get_engine(ctx)
            cfg = eng.config
            # Lazy imports to avoid circular dependencies on module load
            from sq_mcp.tools.batch import _count_databanks_for_project
            from sq_mcp.tools.projects import _scan_projects_fs
            from sq_mcp.tools.state import _read_state, _state_path

            scanned = _scan_projects_fs(cfg.projects_dir)
            workspace_summary = {
                "project_count": len(scanned),
                "total_sqx": sum(
                    _count_databanks_for_project(cfg.projects_dir / p["name"])["sqx_files_total"]
                    for p in scanned[:50]  # cap to avoid huge walks
                ),
                "projects_dir": str(cfg.projects_dir),
            }

            state = _read_state(_state_path(eng))
            state_ns = state.get("data", {})
            state_summary = {
                "state_path": str(_state_path(eng)),
                "last_updated": state.get("last_updated"),
                "namespaces": sorted(state_ns.keys()),
                "namespace_count": len(state_ns),
            }

            health = {
                "sqx_home_exists": cfg.sqx_home.exists(),
                "sqcli_binary_exists": cfg.sqcli.exists(),
                "data_dir_exists": cfg.data_dir.exists(),
                "projects_dir_exists": cfg.projects_dir.exists(),
                "history_dir_exists": cfg.history_dir.exists(),
            }

            tools = mcp._tool_manager._tools  # noqa: SLF001
            cat_counts: dict[str, int] = {}
            for n in tools:
                c = _categorize_tool(n)
                cat_counts[c] = cat_counts.get(c, 0) + 1

            return {
                "ok": True,
                "now": datetime.now(tz=timezone.utc).isoformat(),
                "platform": platform.system(),
                "sqx_home": str(cfg.sqx_home),
                "http_url": cfg.http_url,
                "health_summary": health,
                "workspace_summary": workspace_summary,
                "state_summary": state_summary,
                "tool_count": len(tools),
                "tool_categories": cat_counts,
                "common_workflows_count": len(_COMMON_WORKFLOWS),
                "first_call_suggestion": (
                    "Call environment_health_check, workspace_overview, then "
                    "common_workflows to pick a workflow."
                ),
            }
        except (EngineError, OSError) as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Return a concise system-prompt fragment teaching an AI agent how "
            "to use this plugin effectively (principles, slash commands, safety "
            "rules). Use at session start to bootstrap the agent's understanding."
        )
    )
    async def agent_init_prompt(ctx: Context) -> dict:  # noqa: ARG001
        try:
            return {
                "ok": True,
                "prompt_markdown": _AGENT_INIT_PROMPT,
                "char_count": len(_AGENT_INIT_PROMPT),
                "note": (
                    "Prepend prompt_markdown to your Claude conversation, or read "
                    "AGENT_GUIDE.md for a longer-form version."
                ),
            }
        except OSError as exc:
            return safe_error_payload(exc)

    @mcp.tool(
        description=(
            "Curated list of common multi-tool workflows: name, "
            "what-it-does, and the tool sequence. Returns workflows like "
            "'Build a new portfolio from scratch', 'Audit + ship to MT5', "
            "'Regression-check after a rebuild', 'Recover a corrupted .cfx'. "
            "Use this to discover what's possible end-to-end."
        )
    )
    async def common_workflows(ctx: Context) -> dict:  # noqa: ARG001
        try:
            return {
                "ok": True,
                "workflows": _COMMON_WORKFLOWS,
                "count": len(_COMMON_WORKFLOWS),
            }
        except OSError as exc:
            return safe_error_payload(exc)


_AGENT_INIT_PROMPT = """\
You are working with sq-mcp, a comprehensive MCP plugin for StrategyQuant X (Build 143) + MetaTrader 5.

200+ tools available. Always start a session with `session_bootstrap` to see workspace state.

Key principles:

1. **Layered tools.** Prefer high-level pipelines (`pipeline_export_to_mt5`, `strategy_export_pipeline`,
   `preset_*`) over manual chaining. Use `engine_call` only when no typed tool exists.

2. **Discover with `tool_recommend query="..."`** when unsure which tool to use.
   Use `common_workflows` for end-to-end recipes.

3. **Safety first.** Every cfx_set_* tool snapshots the .cfx before patching.
   Every write tool exits the agent cleanly on validation failure. Use `cfx_lint`
   and `project_precheck` before any project_start.

4. **License is finite.** Confirm before any Builder/Optimizer run that consumes
   trial-license time. Use `preset_quick_smoke` for fast verification.

5. **Cross-session memory.** Use `state_get` / `state_set` for things like
   magic numbers, user preferences, deployment records.

6. **Audit findings have explanations.** Run `explain_finding code=...` to get
   detailed remediation steps for any audit code.

7. **Build 143 has quirks.** Some sqcli commands return "Not implemented" — the
   plugin works around them automatically via filesystem fallbacks. Trust the
   typed tools.

Common one-liners:

- `/sq:bootstrap` — orient at start of session
- `/sq:health` — environment audit
- `/sq:overview` — workspace state
- `/sq:cfx-lint <project>` — config sanity
- `/sq:risk-audit <project>` — full risk audit
- `/sq:rebuild-and-compare <project>` — snapshot → run → compare
- `/sq:ship-to-mt5 <project> <databank> <pack_name>` — full export pipeline

For more, see AGENT_GUIDE.md / EXAMPLES.md / ARCHITECTURE.md / TOOLS.md at the repo root.
"""


_COMMON_WORKFLOWS: list[dict[str, Any]] = [
    {
        "name": "Build a new portfolio from scratch",
        "description": (
            "Configure a Builder project, run it, then triage its output into a "
            "diversified, audit-clean portfolio ready for retest."
        ),
        "steps": [
            "cfx_inspect (see current settings)",
            "cfx_set_data_range / cfx_set_money_management / cfx_set_fitness_criterion",
            "cfx_set_genetic_options (PopulationSize / MaxGenerations / IS-OOS ratio)",
            "cfx_set_max_strategies (cap output)",
            "project_start (then monitor_start with auto_stop=False)",
            "monitor_status (poll until done)",
            "databank_force_sync (databank=Results)",
            "portfolio_summary + portfolio_concentration (sanity check)",
            "portfolio_select_diverse (pick top-N)",
            "strategy_export_pipeline (or pipeline_export_to_mt5 with deploy=False)",
        ],
    },
    {
        "name": "Regression-check after a rebuild",
        "description": "Was the new Builder run actually better than the previous one?",
        "steps": [
            "databank_snapshot_metrics BEFORE the rebuild (label='baseline')",
            "project_start + monitor",
            "databank_snapshot_metrics AFTER (label='post-rebuild')",
            "databank_regression_check (compares the two snapshots)",
            "portfolio_ab_test (also useful — side-by-side summary)",
        ],
    },
    {
        "name": "Audit + ship a portfolio to MT5",
        "description": "End-to-end SQ → MT5 deployment with traffic-light verdicts and magic-number assignment.",
        "steps": [
            "portfolio_select_diverse (or just portfolio_rank top-N)",
            "strategy_export_pipeline (verdict per pick)",
            "mt5_locate (confirm MT5 install detected)",
            "mt5_assign_magic_numbers (if you want to see the map first)",
            "pipeline_export_to_mt5 with deploy=False (plan)",
            "pipeline_export_to_mt5 with deploy=True (actually copy)",
            "mt5_verify_deployment (after MT5 restart)",
        ],
    },
    {
        "name": "Diagnose a flat / broken Builder run",
        "description": "Builder reports zero progress or weird databanks — figure out why.",
        "steps": [
            "monitor_status (engine state + recent log tail)",
            "engine_log_stream (deeper log inspection)",
            "license_info (any license-related errors?)",
            "health_check (broader env probe)",
            "broker_data_integrity (data registry vs disk mismatch?)",
            "data_age_check (is the data even fresh?)",
            "data_bar_density (partial fetch?)",
        ],
    },
    {
        "name": "Workspace data-quality audit",
        "description": "One-shot check of every symbol/TF in the workspace.",
        "steps": [
            "data_workspace_quality_report",
            "broker_data_integrity (cross-check registry vs disk)",
            "data_age_check (stale .dat files)",
            "history_disk_inventory (size on disk)",
        ],
    },
    {
        "name": "Recover from a bad .cfx patch",
        "description": "An automated patcher made things worse; roll back.",
        "steps": [
            "workspace_list_snapshots project=<name>",
            "Compare snapshots by mtime to pick the right rollback point",
            "Manually copy the .cfx.bak.<ts> back to project.cfx",
            "cfx_validate (confirm the rollback is structurally sound)",
            "project_load_and_start (verify it actually runs)",
        ],
    },
    {
        "name": "Backup before a risky bulk operation",
        "description": "Make a portable snapshot of every project config before mass-patching.",
        "steps": [
            "workspace_export_manifest (lightweight state fingerprint)",
            "workspace_export_projects (full tar.gz of cfx files)",
            "Run the bulk operation",
            "Re-run workspace_export_manifest and diff if needed",
        ],
    },
    {
        "name": "Performance assessment of a single strategy",
        "description": "Deep-dive a single .sqx beyond the headline numbers.",
        "steps": [
            "strategy_summarize (headline + audit verdict)",
            "strategy_equity_curve_stats (DD geometry + hit ratio)",
            "strategy_monthly_returns_estimate (return distribution)",
            "strategy_risk_adjusted_metrics (Sharpe / Sortino / Calmar)",
        ],
    },
]


__all__ = [
    "HealthCheckArgs",
    "ToolsCatalogArgs",
    "_COMMON_WORKFLOWS",
    "_categorize_tool",
    "_probe_tcp",
    "_summarize_dir",
    "register",
]
