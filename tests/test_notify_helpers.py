"""Unit tests for notify helpers."""

from __future__ import annotations

from sq_mcp.tools.notify import (
    AuditFindings,
    BuildStatusArgs,
    EmailBodyArgs,
    StatusLineArgs,
    _emoji_for_status,
    _format_audit_block,
    _format_discord_build,
    _format_email_html,
    _format_slack_build,
    _format_status_line,
    _html_escape,
)

# ---- _emoji_for_status -----------------------------------------------------


def test_emoji_for_running() -> None:
    assert _emoji_for_status("Running") == ":runner:"


def test_emoji_for_error() -> None:
    assert _emoji_for_status("Error in build") == ":x:"


def test_emoji_for_stopped() -> None:
    assert _emoji_for_status("Stopped") == ":octagonal_sign:"


def test_emoji_for_finished() -> None:
    assert _emoji_for_status("Finished") == ":white_check_mark:"


def test_emoji_for_unknown() -> None:
    assert _emoji_for_status("???") == ":grey_question:"


# ---- _format_slack_build ---------------------------------------------------


def test_slack_build_includes_project_and_status() -> None:
    out = _format_slack_build(
        BuildStatusArgs(project="MyProj", status={"raw_status": "Running", "fitness": 1.5})
    )
    assert "MyProj" in out
    assert "Running" in out
    assert "1.5" in out


def test_slack_build_skips_none_fields() -> None:
    out = _format_slack_build(
        BuildStatusArgs(project="P", status={"raw_status": "Running", "count": None})
    )
    assert "count" not in out.lower()


def test_slack_build_custom_title() -> None:
    out = _format_slack_build(
        BuildStatusArgs(project="P", status={"raw_status": "Running"}, title="Custom Title")
    )
    assert "Custom Title" in out


# ---- _format_discord_build -------------------------------------------------


def test_discord_build_fields_present() -> None:
    out = _format_discord_build(
        BuildStatusArgs(project="P", status={"raw_status": "Running", "fitness": 2.0})
    )
    assert "title" in out
    field_names = {f["name"] for f in out["fields"]}
    assert "Status" in field_names
    assert "Fitness" in field_names


def test_discord_build_color_red_on_error() -> None:
    out = _format_discord_build(
        BuildStatusArgs(project="P", status={"raw_status": "Error: something"})
    )
    assert out["color"] == 0xE74C3C


def test_discord_build_color_blue_on_finished() -> None:
    out = _format_discord_build(
        BuildStatusArgs(project="P", status={"raw_status": "Finished"})
    )
    assert out["color"] == 0x3498DB


# ---- _format_audit_block ---------------------------------------------------


def test_audit_block_no_findings() -> None:
    out = _format_audit_block(AuditFindings(findings=[], title="Audit"))
    assert "No issues found" in out


def test_audit_block_groups_by_severity() -> None:
    findings = [
        {"severity": "critical", "code": "X", "message": "bad"},
        {"severity": "info", "code": "Y", "message": "noise"},
        {"severity": "critical", "code": "Z", "message": "worse"},
    ]
    out = _format_audit_block(AuditFindings(findings=findings, title="A"))
    assert "critical=2" in out
    assert "info=1" in out


def test_audit_block_sorts_critical_first() -> None:
    findings = [
        {"severity": "info", "code": "INFO1", "message": "info one"},
        {"severity": "critical", "code": "CRIT1", "message": "crit one"},
    ]
    out = _format_audit_block(AuditFindings(findings=findings, title="A"))
    info_idx = out.index("INFO1")
    crit_idx = out.index("CRIT1")
    assert crit_idx < info_idx


def test_audit_block_truncates_at_10() -> None:
    findings = [{"severity": "low", "code": f"C{i}", "message": "m"} for i in range(15)]
    out = _format_audit_block(AuditFindings(findings=findings, title="A"))
    assert "5 more" in out


# ---- _format_email_html ----------------------------------------------------


def test_email_html_smoke() -> None:
    out = _format_email_html(
        EmailBodyArgs(
            title="Daily report",
            sections=[
                {"heading": "Summary", "body": "All systems nominal."},
                {"heading": "Strategies", "body": ["S1: 1.2", "S2: 0.9"]},
            ],
        )
    )
    assert "<html>" in out
    assert "Daily report" in out
    assert "S1: 1.2" in out
    assert "<ul>" in out


def test_email_html_escapes_unsafe_chars() -> None:
    out = _format_email_html(
        EmailBodyArgs(title="<script>alert(1)</script>", sections=[{"heading": "h", "body": "b"}])
    )
    # The original tag must be HTML-escaped, not present literally
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


# ---- _html_escape ----------------------------------------------------------


def test_html_escape_basic() -> None:
    assert _html_escape("<b>") == "&lt;b&gt;"


def test_html_escape_ampersand() -> None:
    assert _html_escape("A & B") == "A &amp; B"


def test_html_escape_quote() -> None:
    assert _html_escape('"hi"') == "&quot;hi&quot;"


# ---- _format_status_line ---------------------------------------------------


def test_status_line_basic() -> None:
    out = _format_status_line(
        StatusLineArgs(project="P", status={"raw_status": "Running", "fitness": 1.5, "count": 7})
    )
    assert "[P]" in out
    assert "Running" in out
    assert "fitness=1.5" in out
    assert "count=7" in out


def test_status_line_skips_none() -> None:
    out = _format_status_line(StatusLineArgs(project="P", status={"raw_status": "Running"}))
    assert "fitness" not in out
    assert "count" not in out
