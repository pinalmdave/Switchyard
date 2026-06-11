"""CI gate tests (F-RPT-03): the exit-code matrix for ``switchyard check``."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from helpers import FABLE, OPUS
from switchyard.ci import parse_rate, run_check
from switchyard.cli import main
from switchyard.ledger import Ledger, RequestRecord


def seed(requests: int, fallbacks: int) -> None:
    with Ledger() as ledger:
        for i in range(requests):
            entry = ledger.append_request(
                RequestRecord(requested_model=FABLE, served_model=FABLE, prompt=f"r{i}")
            )
            if i < fallbacks:
                ledger.record_fallback(entry.sequence, "declared", 1.0, FABLE, OPUS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.02", 0.02), ("2%", 0.02), ("0", 0.0), ("100%", 1.0), ("0.5", 0.5)],
)
def test_parse_rate(value: str, expected: float) -> None:
    assert parse_rate(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["abc", "-1", "1.5", "200%", "%"])
def test_parse_rate_rejects_garbage(value: str) -> None:
    with pytest.raises(ValueError):
        parse_rate(value)


def test_run_check_pass_and_fail() -> None:
    seed(requests=100, fallbacks=3)
    with Ledger() as ledger:
        assert run_check(ledger, max_rate=0.05).ok
        result = run_check(ledger, max_rate=0.02)
    assert not result.ok
    assert result.fallback_rate == pytest.approx(0.03)
    assert result.total_requests == 100
    assert result.fallback_events == 3


def test_run_check_empty_ledger_passes() -> None:
    with Ledger() as ledger:
        result = run_check(ledger, max_rate=0.0)
    assert result.ok
    assert result.total_requests == 0


def test_run_check_boundary_is_inclusive() -> None:
    seed(requests=100, fallbacks=2)
    with Ledger() as ledger:
        assert run_check(ledger, max_rate=0.02).ok  # exactly at threshold passes


# -- CLI exit-code matrix ------------------------------------------------------------


def test_cli_check_passes(runner: CliRunner) -> None:
    seed(requests=100, fallbacks=1)
    result = runner.invoke(main, ["check", "--max-rate", "2%"])
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_cli_check_fails_on_breach(runner: CliRunner) -> None:
    seed(requests=100, fallbacks=5)
    result = runner.invoke(main, ["check", "--max-rate", "2%"])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_check_json_output(runner: CliRunner) -> None:
    seed(requests=10, fallbacks=1)
    result = runner.invoke(main, ["check", "--max-rate", "0.02", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["fallback_rate"] == 0.1
    assert payload["max_rate"] == 0.02


def test_cli_check_bad_rate_is_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(main, ["check", "--max-rate", "lots"])
    assert result.exit_code == 2
    assert "invalid rate" in result.output


def test_cli_check_bad_since_is_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(main, ["check", "--max-rate", "2%", "--since", "yesterday"])
    assert result.exit_code == 2


def test_cli_check_requires_max_rate(runner: CliRunner) -> None:
    result = runner.invoke(main, ["check"])
    assert result.exit_code == 2


def test_cli_check_since_window(runner: CliRunner) -> None:
    seed(requests=10, fallbacks=5)  # all recent -> breach inside any window
    result = runner.invoke(main, ["check", "--max-rate", "2%", "--since", "24h"])
    assert result.exit_code == 1
