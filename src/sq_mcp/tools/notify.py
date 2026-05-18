"""Notification formatters for build statuses, audit results, deployments.

Produces ready-to-paste Slack / Discord / email markdown bodies from
the structured outputs of other tools. No HTTP calls — the agent
or user is expected to post the produced text via whatever transport
they already use.

Tools:

- ``notify_format_slack_build_status`` — given a project_status dict (as
  returned by ``project_status``), produce a Slack-flavored markdown
  string suitable for ``chat.postMessage`` or a webhook body.
- ``notify_format_discord_build_status`` — same payload, Discord embed.
- ``notify_format_audit_summary`` — given an audit findings list (from
  ``analyze_mq5_file`` / ``portfolio_audit``), produce a digestible
  Slack/Discord block.
- ``notify_format_email_html`` — full HTML email body for a deployment
  summary or a multi-section update.
- ``notify_format_status_line`` — one-line plain-text summary (good
  for cron-job log lines or a status bar).

Each tool returns ``{ok: True, text: ...}`` (or ``html`` /
``blocks`` depending on the format). Pure string formatting — read-only.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---- argument schemas ------------------------------------------------------


class BuildStatusArgs(BaseModel):
    project: str
    status: dict[str, Any]
    title: str | None = None


class AuditFindings(BaseModel):
    findings: list[dict[str, Any]] = Field(..., max_length=500)
    title: str = "Audit summary"
    target: str | None = None


class EmailBodyArgs(BaseModel):
    title: str
    sections: list[dict[str, Any]] = Field(..., min_length=1, max_length=20)


class StatusLineArgs(BaseModel):
    project: str
    status: dict[str, Any]


# ---- helpers ---------------------------------------------------------------


def _emoji_for_status(status_raw: str) -> str:
    s = (status_raw or "").lower()
    if "error" in s or "fail" in s:
        return ":x:"
    if "not running" in s or "stopped" in s:
        return ":octagonal_sign:"
    if "finished" in s or "complete" in s:
        return ":white_check_mark:"
    if "running" in s or "active" in s:
        return ":runner:"
    return ":grey_question:"


def _format_slack_build(args: BuildStatusArgs) -> str:
    title = args.title or f"Build update — {args.project}"
    raw = args.status.get("raw_status") or args.status.get("status") or "unknown"
    emoji = _emoji_for_status(str(raw))
    lines = [f"*{title}* {emoji}", f"> Project: `{args.project}`", f"> Status: `{raw}`"]
    extras = ("fitness", "count", "elapsed", "task")
    for k in extras:
        if k in args.status and args.status[k] is not None:
            lines.append(f"> {k.capitalize()}: `{args.status[k]}`")
    return "\n".join(lines)


def _format_discord_build(args: BuildStatusArgs) -> dict[str, Any]:
    raw = args.status.get("raw_status") or args.status.get("status") or "unknown"
    color = 0x2ECC71 if "running" in str(raw).lower() else 0xE67E22
    if "error" in str(raw).lower() or "fail" in str(raw).lower():
        color = 0xE74C3C
    if "finished" in str(raw).lower() or "complete" in str(raw).lower():
        color = 0x3498DB

    fields = [{"name": "Status", "value": f"`{raw}`", "inline": True}]
    for k in ("fitness", "count", "elapsed"):
        v = args.status.get(k)
        if v is not None:
            fields.append({"name": k.capitalize(), "value": f"`{v}`", "inline": True})

    return {
        "title": args.title or f"Build update — {args.project}",
        "color": color,
        "fields": fields,
        "footer": {"text": f"project: {args.project}"},
    }


def _format_audit_block(args: AuditFindings) -> str:
    if not args.findings:
        return f"*{args.title}* :white_check_mark:\nNo issues found."
    sev_counts: dict[str, int] = {}
    for f in args.findings:
        s = (f.get("severity") or "info").lower()
        sev_counts[s] = sev_counts.get(s, 0) + 1
    parts = [f"*{args.title}* :rotating_light:"]
    if args.target:
        parts.append(f"> Target: `{args.target}`")
    parts.append("> Summary: " + ", ".join(f"{k}={v}" for k, v in sorted(sev_counts.items())))
    # First 10 findings as bullets
    parts.append("")
    severities_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    sorted_f = sorted(
        args.findings, key=lambda f: severities_order.get((f.get("severity") or "info").lower(), 99)
    )
    for f in sorted_f[:10]:
        sev = (f.get("severity") or "info").lower()
        code = f.get("code") or "?"
        msg = f.get("message") or ""
        parts.append(f"• `{sev.upper()}` *{code}* — {msg}")
    if len(args.findings) > 10:
        parts.append(f"… and {len(args.findings) - 10} more.")
    return "\n".join(parts)


def _format_email_html(args: EmailBodyArgs) -> str:
    lines: list[str] = [
        "<html><body style=\"font-family:Arial,Helvetica,sans-serif;color:#333\">",
        f"<h1 style=\"color:#1a4a8a\">{_html_escape(args.title)}</h1>",
    ]
    for sec in args.sections:
        heading = sec.get("heading") or ""
        body = sec.get("body") or ""
        lines.append(f"<h2 style=\"color:#444\">{_html_escape(heading)}</h2>")
        # If body is a list, render as <ul>
        if isinstance(body, list):
            lines.append("<ul>")
            for item in body:
                lines.append(f"<li>{_html_escape(str(item))}</li>")
            lines.append("</ul>")
        else:
            lines.append(f"<p>{_html_escape(str(body))}</p>")
    lines.append("</body></html>")
    return "\n".join(lines)


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _format_status_line(args: StatusLineArgs) -> str:
    raw = args.status.get("raw_status") or args.status.get("status") or "unknown"
    fit = args.status.get("fitness")
    count = args.status.get("count")
    parts = [f"[{args.project}]", str(raw)]
    if fit is not None:
        parts.append(f"fitness={fit}")
    if count is not None:
        parts.append(f"count={count}")
    return " ".join(parts)


# ---- MCP registration ------------------------------------------------------


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        description=(
            "Format a Slack-flavored markdown message from a project status dict "
            "(as returned by project_status). Includes a status emoji and the "
            "common fields (status, fitness, count, elapsed). Returns text only "
            "— the caller posts it. Pure formatting — no HTTP."
        )
    )
    async def notify_format_slack_build_status(args: BuildStatusArgs) -> dict:
        return {"ok": True, "text": _format_slack_build(args), "format": "slack"}

    @mcp.tool(
        description=(
            "Format a Discord embed dict from a project status. Caller wraps it "
            "in `{embeds: [...]}` and POSTs to the webhook. Returns the embed "
            "object only."
        )
    )
    async def notify_format_discord_build_status(args: BuildStatusArgs) -> dict:
        return {"ok": True, "embed": _format_discord_build(args), "format": "discord"}

    @mcp.tool(
        description=(
            "Format an audit-findings summary as Slack/Discord markdown. Counts "
            "by severity, lists the top 10 worst findings, returns a single "
            "block of text. Pure formatting."
        )
    )
    async def notify_format_audit_summary(args: AuditFindings) -> dict:
        return {"ok": True, "text": _format_audit_block(args), "format": "slack-or-discord"}

    @mcp.tool(
        description=(
            "Build an HTML email body from a title + section list. Each section "
            "is {heading, body}; body may be a string or a list (rendered as "
            "<ul>). Returns ready-to-send HTML. Pure formatting."
        )
    )
    async def notify_format_email_html(args: EmailBodyArgs) -> dict:
        return {"ok": True, "html": _format_email_html(args), "format": "email-html"}

    @mcp.tool(
        description=(
            "One-line plain-text status (cron-friendly). [project] status "
            "fitness=X count=Y. Useful for shell scripts or status bars."
        )
    )
    async def notify_format_status_line(args: StatusLineArgs) -> dict:
        return {"ok": True, "text": _format_status_line(args), "format": "plain"}
