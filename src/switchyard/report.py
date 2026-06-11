"""Fallback-rate reports and the live audit view (F-RPT-01, F-RPT-02).

``switchyard report`` summarizes the local ledger: totals, fallback rate,
by-method split (with confidence always shown — heuristics are never dressed
up as facts), top tripping groups, and the estimated token cost of retrying
the fallen-back requests on the frontier model.

``switchyard audit`` confirms capture is working and then watches the ledger,
printing fallback events as they land.
"""

from __future__ import annotations

import json
import re
import time as time_module
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rich.console import Console
from rich.table import Table

from switchyard.ledger import Ledger

_SINCE = re.compile(r"^(\d+)([mhdw])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}

GROUP_COLUMNS = {
    "task_type": "task_type",
    "engagement": "engagement",
    "model": "requested_model",
}
UNTAGGED = "(untagged)"


def parse_since(value: str) -> timedelta:
    """Parse ``30m`` / ``24h`` / ``7d`` / ``2w`` into a timedelta."""
    match = _SINCE.match(value.strip().lower())
    if not match:
        raise ValueError(f"invalid --since value {value!r}; use e.g. 30m, 24h, 7d, 2w")
    amount, unit = match.groups()
    return timedelta(**{_UNITS[unit]: int(amount)})


def _cutoff_iso(since: timedelta) -> str:
    return (datetime.now(UTC) - since).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class GroupStat:
    """Requests/fallbacks for one group (task type, engagement, or model)."""

    key: str
    requests: int
    fallbacks: int

    @property
    def rate(self) -> float:
        return self.fallbacks / self.requests if self.requests else 0.0


@dataclass(frozen=True)
class Report:
    """Everything ``switchyard report`` shows, in one renderable value."""

    since: str | None
    privacy_mode: str
    total_requests: int
    fallback_events: int
    by_method: dict[str, dict[str, float]]
    group_by: str
    groups: list[GroupStat]
    estimated_retry_tokens: int

    @property
    def fallback_rate(self) -> float:
        return self.fallback_events / self.total_requests if self.total_requests else 0.0


def build_report(ledger: Ledger, since: str | None = None, by: str = "task_type") -> Report:
    """Aggregate the ledger into a :class:`Report`.

    ``since`` is a duration string (``7d``); ``by`` is one of
    ``task_type | engagement | model``.
    """
    if by not in GROUP_COLUMNS:
        raise ValueError(f"--by must be one of {', '.join(GROUP_COLUMNS)}; got {by!r}")
    column = GROUP_COLUMNS[by]
    conn = ledger._conn  # package-internal read-only analytics
    params: list[str] = []
    where = ""
    if since is not None:
        where = "WHERE r.timestamp >= ?"
        params.append(_cutoff_iso(parse_since(since)))

    total = conn.execute(f"SELECT COUNT(*) AS n FROM requests r {where}", params).fetchone()["n"]
    events = conn.execute(
        f"SELECT COUNT(*) AS n FROM fallback_events e"
        f" JOIN requests r ON r.sequence = e.request_sequence {where}",
        params,
    ).fetchone()["n"]

    by_method: dict[str, dict[str, float]] = {}
    for row in conn.execute(
        f"SELECT e.detection_method AS method, COUNT(*) AS n, AVG(e.confidence) AS avg_conf"
        f" FROM fallback_events e JOIN requests r ON r.sequence = e.request_sequence"
        f" {where} GROUP BY e.detection_method",
        params,
    ):
        by_method[row["method"]] = {
            "events": int(row["n"]),
            "avg_confidence": round(float(row["avg_conf"]), 3),
        }

    groups = [
        GroupStat(key=row["grp"] or UNTAGGED, requests=int(row["n"]), fallbacks=int(row["f"]))
        for row in conn.execute(
            f"SELECT r.{column} AS grp, COUNT(*) AS n,"
            f" SUM(EXISTS(SELECT 1 FROM fallback_events e"
            f"     WHERE e.request_sequence = r.sequence)) AS f"
            f" FROM requests r {where} GROUP BY r.{column}"
            f" ORDER BY f DESC, n DESC LIMIT 10",
            params,
        )
    ]

    retry_tokens = conn.execute(
        "SELECT COALESCE(SUM("
        " COALESCE(json_extract(r.payload, '$.input_tokens'), 0)"
        " + COALESCE(json_extract(r.payload, '$.output_tokens'), 0)), 0) AS t"
        " FROM requests r"
        " WHERE EXISTS(SELECT 1 FROM fallback_events e WHERE e.request_sequence = r.sequence)"
        + (" AND r.timestamp >= ?" if since is not None else ""),
        params,
    ).fetchone()["t"]

    return Report(
        since=since,
        privacy_mode=ledger.privacy_mode.value,
        total_requests=int(total),
        fallback_events=int(events),
        by_method=by_method,
        group_by=by,
        groups=groups,
        estimated_retry_tokens=int(retry_tokens),
    )


def to_json(report: Report) -> str:
    """Serialize a report for ``--format json`` (stable key order)."""
    return json.dumps(
        {
            "since": report.since,
            "privacy_mode": report.privacy_mode,
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
        },
        indent=2,
        sort_keys=True,
    )


def to_markdown(report: Report) -> str:
    """Render a report for ``--format md``."""
    window = f" (last {report.since})" if report.since else ""
    lines = [
        f"# Switchyard fallback report{window}",
        "",
        f"- Total requests: {report.total_requests}",
        f"- Fallback events: {report.fallback_events}",
        f"- Fallback rate: {report.fallback_rate:.2%}",
        f"- Privacy mode: {report.privacy_mode}",
        f"- Estimated retry token cost: {report.estimated_retry_tokens:,} tokens",
        "",
        "## By detection method",
        "",
        "| Method | Events | Avg confidence |",
        "|---|---:|---:|",
    ]
    for method in sorted(report.by_method):
        stats = report.by_method[method]
        lines.append(f"| {method} | {stats['events']:.0f} | {stats['avg_confidence']:.2f} |")
    if not report.by_method:
        lines.append("| (none) | 0 | - |")
    lines += [
        "",
        f"## By {report.group_by}",
        "",
        f"| {report.group_by} | Requests | Fallbacks | Rate |",
        "|---|---:|---:|---:|",
    ]
    for group in report.groups:
        lines.append(f"| {group.key} | {group.requests} | {group.fallbacks} | {group.rate:.2%} |")
    if not report.groups:
        lines.append("| (no traffic) | 0 | 0 | 0.00% |")
    return "\n".join(lines) + "\n"


def render_terminal(report: Report, console: Console) -> None:
    """Rich terminal rendering (ASCII-safe)."""
    window = f" (last {report.since})" if report.since else ""
    headline = Table(title=f"Switchyard fallback report{window}")
    headline.add_column("Metric")
    headline.add_column("Value", justify="right")
    headline.add_row("Total requests", str(report.total_requests))
    headline.add_row("Fallback events", str(report.fallback_events))
    headline.add_row("Fallback rate", f"{report.fallback_rate:.2%}")
    headline.add_row("Privacy mode", report.privacy_mode)
    headline.add_row("Est. retry token cost", f"{report.estimated_retry_tokens:,}")
    console.print(headline)

    methods = Table(title="By detection method")
    methods.add_column("Method")
    methods.add_column("Events", justify="right")
    methods.add_column("Avg confidence", justify="right")
    for method in sorted(report.by_method):
        stats = report.by_method[method]
        methods.add_row(method, f"{stats['events']:.0f}", f"{stats['avg_confidence']:.2f}")
    console.print(methods)

    groups = Table(title=f"By {report.group_by}")
    groups.add_column(report.group_by)
    groups.add_column("Requests", justify="right")
    groups.add_column("Fallbacks", justify="right")
    groups.add_column("Rate", justify="right")
    for group in report.groups:
        groups.add_row(group.key, str(group.requests), str(group.fallbacks), f"{group.rate:.2%}")
    console.print(groups)


# -- audit (F-RPT-02) ------------------------------------------------------------


def capture_status(ledger: Ledger) -> dict[str, Any]:
    """One-shot 'is capture working' summary for ``switchyard audit``."""
    conn = ledger._conn
    last = conn.execute("SELECT MAX(timestamp) AS ts FROM requests").fetchone()["ts"]
    return {
        "ledger_path": str(ledger.path),
        "privacy_mode": ledger.privacy_mode.value,
        "total_requests": ledger.request_count(),
        "fallback_events": ledger.fallback_count(),
        "last_request_at": last,
        "capturing": last is not None,
    }


def follow_fallbacks(
    ledger: Ledger,
    console: Console,
    poll_interval: float = 1.0,
    max_polls: int | None = None,
    from_id: int | None = None,
) -> int:
    """Print fallback events as they land. Returns the number printed.

    ``max_polls`` bounds the loop for tests; ``None`` means run until
    interrupted (Ctrl-C). ``from_id`` replays events after that id
    (default: only events newer than the call).
    """
    conn = ledger._conn
    if from_id is None:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS last FROM fallback_events").fetchone()
        last_id = int(row["last"])
    else:
        last_id = from_id
    printed = 0
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        rows = conn.execute(
            "SELECT id, created_at, detection_method, confidence, requested_model, served_model"
            " FROM fallback_events WHERE id > ? ORDER BY id",
            (last_id,),
        ).fetchall()
        for event in rows:
            last_id = int(event["id"])
            printed += 1
            served = event["served_model"] or "(unknown)"
            console.print(
                f"[bold red]FALLBACK[/bold red] {event['created_at']}  "
                f"{event['requested_model']} -> {served}  "
                f"method={event['detection_method']} confidence={event['confidence']:.2f}"
            )
        if max_polls is None or polls < max_polls:
            time_module.sleep(poll_interval)
    return printed
