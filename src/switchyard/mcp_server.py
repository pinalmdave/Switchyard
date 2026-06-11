"""Read-only MCP server over the local ledger (F-PLG-03) — extra: ``[mcp]``.

``switchyard mcp`` starts a stdio MCP server exposing read-only tools so any MCP
client (Claude Code and others) can query the ledger:

- ``get_fallback_summary`` — totals, rate, by-method split (F-RPT-01)
- ``list_fallback_events`` — recent fallback events with method + confidence
- ``suggest_rescope`` — template matches + suggested reframe for a prompt (F-RSC-02)
- ``verify_ledger`` — walk the hash chain and report integrity (F-LGR-01)

Read-only by design: the server never sends an API request and never mutates the
ledger (``suggest_rescope`` here does not record, unlike the CLI). Nothing leaves
the machine.
"""

from __future__ import annotations

from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - only without the extra
    raise ImportError(
        "MCP server needs the optional extra: pip install 'switchyard-ai[mcp]'"
    ) from exc

from switchyard.ledger import Ledger
from switchyard.report import build_report
from switchyard.rescope import match_templates, render_suggestion


def get_fallback_summary(since: str | None = None, by: str = "task_type") -> dict[str, Any]:
    """Return fallback totals, rate, by-method split, and top groups.

    ``since`` is a window like ``24h``/``7d`` (default: all time); ``by`` is one
    of ``task_type | engagement | model``.
    """
    with Ledger() as ledger:
        report = build_report(ledger, since=since, by=by)
    return {
        "total_requests": report.total_requests,
        "fallback_events": report.fallback_events,
        "fallback_rate": round(report.fallback_rate, 4),
        "by_method": report.by_method,
        "group_by": report.group_by,
        "groups": [
            {
                "key": g.key,
                "requests": g.requests,
                "fallbacks": g.fallbacks,
                "rate": round(g.rate, 4),
            }
            for g in report.groups
        ],
        "estimated_retry_tokens": report.estimated_retry_tokens,
        "privacy_mode": report.privacy_mode,
    }


def list_fallback_events(limit: int = 20) -> dict[str, Any]:
    """Return the most recent fallback events (method + confidence always shown)."""
    limit = max(1, min(int(limit), 500))
    with Ledger() as ledger:
        rows = ledger._conn.execute(
            "SELECT id, created_at, detection_method, confidence, requested_model, served_model"
            " FROM fallback_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "count": len(rows),
        "events": [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "detection_method": row["detection_method"],
                "confidence": row["confidence"],
                "requested_model": row["requested_model"],
                "served_model": row["served_model"],
            }
            for row in rows
        ],
    }


def suggest_rescope(prompt: str, task_type: str | None = None) -> dict[str, Any]:
    """Match re-scope templates for a prompt and return a suggested reframe.

    Read-only: unlike the CLI, this does not record the suggestion in the ledger.
    """
    matches = match_templates(prompt, task_type=task_type)
    top = matches[0].template if matches else None
    return {
        "matches": [
            {
                "name": m.template.name,
                "task_type": m.template.task_type,
                "score": m.score,
                "matched_signals": m.matched_signals,
                "rationale": m.template.rationale.strip(),
            }
            for m in matches
        ],
        "suggestion": render_suggestion(top, prompt) if top is not None else None,
    }


def verify_ledger() -> dict[str, Any]:
    """Walk the hash chain and report integrity (first broken link, if any)."""
    with Ledger() as ledger:
        result = ledger.verify()
    return {
        "ok": result.ok,
        "entries_checked": result.entries_checked,
        "first_broken_sequence": result.first_broken_sequence,
        "error": result.error,
    }


def build_server() -> FastMCP:
    """Construct the FastMCP server with the four read-only tools registered."""
    server = FastMCP("switchyard")
    server.tool()(get_fallback_summary)
    server.tool()(list_fallback_events)
    server.tool()(suggest_rescope)
    server.tool()(verify_ledger)
    return server


def main() -> None:
    """Entry point for ``switchyard mcp`` — serve over stdio."""
    build_server().run()


if __name__ == "__main__":
    main()
