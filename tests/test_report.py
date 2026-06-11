"""Report + audit tests (F-RPT-01/02): golden outputs over a deterministic ledger."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner
from rich.console import Console

from helpers import FABLE, OPUS
from switchyard.cli import main
from switchyard.ledger import Ledger, RequestRecord
from switchyard.report import (
    build_report,
    capture_status,
    follow_fallbacks,
    parse_since,
    to_json,
    to_markdown,
)


def seed_ledger() -> None:
    """10 requests / 3 fallbacks across two task types and two engagements."""
    with Ledger() as ledger:
        ledger.set_privacy_mode("metadata")

        def add(
            task_type: str,
            engagement: str,
            tokens: tuple[int, int],
            fallback: tuple[str, float] | None = None,
        ) -> None:
            entry = ledger.append_request(
                RequestRecord(
                    requested_model=FABLE,
                    served_model=OPUS if fallback else FABLE,
                    prompt="seeded",
                    input_tokens=tokens[0],
                    output_tokens=tokens[1],
                    latency_ms=1000.0,
                    engagement=engagement,
                    task_type=task_type,
                )
            )
            if fallback:
                method, confidence = fallback
                ledger.record_fallback(
                    entry.sequence, method, confidence, FABLE, OPUS if fallback else FABLE
                )

        for _ in range(5):
            add("triage", "acme", (100, 200))
        add("exploit-analysis", "acme", (400, 600), fallback=("declared", 1.0))
        add("exploit-analysis", "acme", (400, 600), fallback=("declared", 1.0))
        add("exploit-analysis", "acme", (400, 600))
        add("triage", "beta", (50, 50), fallback=("heuristic", 0.65))
        add("triage", "beta", (50, 50))


GOLDEN_MD = """# Switchyard fallback report

- Total requests: 10
- Fallback events: 3
- Fallback rate: 30.00%
- Privacy mode: metadata
- Estimated retry token cost: 2,100 tokens

## By detection method

| Method | Events | Avg confidence |
|---|---:|---:|
| declared | 2 | 1.00 |
| heuristic | 1 | 0.65 |

## By task_type

| task_type | Requests | Fallbacks | Rate |
|---|---:|---:|---:|
| exploit-analysis | 3 | 2 | 66.67% |
| triage | 7 | 1 | 14.29% |
"""


def test_markdown_golden() -> None:
    seed_ledger()
    with Ledger() as ledger:
        report = build_report(ledger)
    assert to_markdown(report) == GOLDEN_MD


def test_json_output() -> None:
    seed_ledger()
    with Ledger() as ledger:
        report = build_report(ledger)
    data = json.loads(to_json(report))
    assert data["total_requests"] == 10
    assert data["fallback_events"] == 3
    assert data["fallback_rate"] == 0.3
    assert data["by_method"]["declared"] == {"events": 2, "avg_confidence": 1.0}
    assert data["by_method"]["heuristic"] == {"events": 1, "avg_confidence": 0.65}
    assert data["estimated_retry_tokens"] == 2100
    assert data["groups"][0] == {
        "key": "exploit-analysis",
        "requests": 3,
        "fallbacks": 2,
        "rate": 0.6667,
    }


def test_group_by_engagement_and_model() -> None:
    seed_ledger()
    with Ledger() as ledger:
        by_engagement = build_report(ledger, by="engagement")
        by_model = build_report(ledger, by="model")
    keys = {g.key: g for g in by_engagement.groups}
    assert keys["acme"].requests == 8
    assert keys["acme"].fallbacks == 2
    assert keys["beta"].fallbacks == 1
    assert by_model.groups[0].key == FABLE
    assert by_model.groups[0].requests == 10


def test_untagged_grouping_in_hash_mode() -> None:
    with Ledger() as ledger:  # default hash mode drops tags
        ledger.append_request(RequestRecord(requested_model=FABLE, task_type="triage"))
        report = build_report(ledger)
    assert report.groups[0].key == "(untagged)"


def test_since_filters_window() -> None:
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="milliseconds")
    with Ledger() as ledger:
        entry = ledger.append_request(RequestRecord(requested_model=FABLE, timestamp=old))
        ledger.record_fallback(entry.sequence, "declared", 1.0, FABLE, OPUS)
        ledger.append_request(RequestRecord(requested_model=FABLE))
        full = build_report(ledger)
        recent = build_report(ledger, since="24h")
    assert full.total_requests == 2
    assert full.fallback_events == 1
    assert recent.total_requests == 1
    assert recent.fallback_events == 0


def test_empty_ledger_report() -> None:
    with Ledger() as ledger:
        report = build_report(ledger)
    assert report.total_requests == 0
    assert report.fallback_rate == 0.0
    assert "| (none) | 0 | - |" in to_markdown(report)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("2w", timedelta(weeks=2)),
    ],
)
def test_parse_since(value: str, expected: timedelta) -> None:
    assert parse_since(value) == expected


@pytest.mark.parametrize("value", ["", "7", "d7", "7y", "soon"])
def test_parse_since_rejects_garbage(value: str) -> None:
    with pytest.raises(ValueError, match="invalid --since"):
        parse_since(value)


def test_build_report_rejects_unknown_group() -> None:
    with Ledger() as ledger, pytest.raises(ValueError, match="--by must be one of"):
        build_report(ledger, by="vibes")


# -- audit ------------------------------------------------------------------------


def test_capture_status_empty_then_active() -> None:
    with Ledger() as ledger:
        assert capture_status(ledger)["capturing"] is False
        ledger.append_request(RequestRecord(requested_model=FABLE))
        status = capture_status(ledger)
    assert status["capturing"] is True
    assert status["total_requests"] == 1


def test_follow_fallbacks_replays_events() -> None:
    seed_ledger()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=200)
    with Ledger() as ledger:
        printed = follow_fallbacks(ledger, console, max_polls=1, from_id=0)
    assert printed == 3
    output = buffer.getvalue()
    assert output.count("FALLBACK") == 3
    assert "method=declared confidence=1.00" in output
    assert "method=heuristic confidence=0.65" in output


def test_follow_fallbacks_only_new_by_default() -> None:
    seed_ledger()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=200)
    with Ledger() as ledger:
        printed = follow_fallbacks(ledger, console, max_polls=1)
    assert printed == 0  # existing events are history, not news


# -- CLI ----------------------------------------------------------------------------


def test_cli_report_md_golden(runner: CliRunner) -> None:
    seed_ledger()
    result = runner.invoke(main, ["report", "--format", "md"])
    assert result.exit_code == 0
    assert result.output == GOLDEN_MD


def test_cli_report_table(runner: CliRunner) -> None:
    seed_ledger()
    result = runner.invoke(main, ["report"])
    assert result.exit_code == 0
    assert "Switchyard fallback report" in result.output
    assert "30.00%" in result.output
    assert "exploit-analysis" in result.output


def test_cli_report_json(runner: CliRunner) -> None:
    seed_ledger()
    result = runner.invoke(main, ["report", "--format", "json", "--since", "7d"])
    assert result.exit_code == 0
    assert json.loads(result.output)["total_requests"] == 10


def test_cli_report_rejects_bad_since(runner: CliRunner) -> None:
    result = runner.invoke(main, ["report", "--since", "yesterday"])
    assert result.exit_code != 0
    assert "invalid --since" in result.output


def test_cli_audit_once(runner: CliRunner) -> None:
    result = runner.invoke(main, ["audit", "--once"])
    assert result.exit_code == 0
    assert "no traffic captured yet" in result.output
    seed_ledger()
    result = runner.invoke(main, ["audit", "--once"])
    assert result.exit_code == 0
    assert "capture OK" in result.output
    assert "fallback events so far: 3" in result.output
