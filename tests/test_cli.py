"""CLI tests: config, verify, and demo --simulate (S1 skeleton)."""

from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from switchyard import __version__
from switchyard.cli import main
from switchyard.ledger import Ledger, default_ledger_path


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_commands(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("config", "verify", "demo"):
        assert command in result.output


# -- config -------------------------------------------------------------------


def test_config_get_default_privacy(runner: CliRunner) -> None:
    result = runner.invoke(main, ["config", "get", "privacy"])
    assert result.exit_code == 0
    assert result.output.strip() == "hash"


def test_config_get_json(runner: CliRunner) -> None:
    result = runner.invoke(main, ["config", "get", "privacy", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"privacy": "hash"}


def test_config_set_and_get_roundtrip(runner: CliRunner) -> None:
    result = runner.invoke(main, ["config", "set", "privacy", "metadata"])
    assert result.exit_code == 0
    assert "privacy = metadata" in result.output
    result = runner.invoke(main, ["config", "get", "privacy"])
    assert result.output.strip() == "metadata"


def test_config_set_full_warns_loudly(runner: CliRunner) -> None:
    result = runner.invoke(main, ["config", "set", "privacy", "full"])
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "prompt bodies" in result.output


def test_config_set_invalid_value(runner: CliRunner) -> None:
    result = runner.invoke(main, ["config", "set", "privacy", "everything"])
    assert result.exit_code != 0
    assert "privacy must be one of" in result.output


def test_config_get_unknown_key(runner: CliRunner) -> None:
    result = runner.invoke(main, ["config", "get", "nope"])
    assert result.exit_code != 0


# -- verify ---------------------------------------------------------------------


def test_verify_empty_ledger_ok(runner: CliRunner) -> None:
    result = runner.invoke(main, ["verify"])
    assert result.exit_code == 0
    assert "chain verified" in result.output


def test_verify_json(runner: CliRunner) -> None:
    runner.invoke(main, ["demo", "--simulate", "--requests", "10"])
    result = runner.invoke(main, ["verify", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["entries_checked"] == 10
    assert parsed["first_broken_sequence"] is None


def test_verify_tampered_ledger_fails(runner: CliRunner) -> None:
    runner.invoke(main, ["demo", "--simulate", "--requests", "10"])
    conn = sqlite3.connect(default_ledger_path())
    with conn:
        conn.execute("UPDATE requests SET prev_hash = ? WHERE sequence = 5", ("0" * 64,))
    conn.close()
    result = runner.invoke(main, ["verify"])
    assert result.exit_code == 1
    assert "chain broken" in result.output
    json_result = runner.invoke(main, ["verify", "--json"])
    assert json_result.exit_code == 1
    assert json.loads(json_result.output)["ok"] is False


# -- demo -----------------------------------------------------------------------


def test_demo_requires_simulate_flag(runner: CliRunner) -> None:
    result = runner.invoke(main, ["demo"])
    assert result.exit_code != 0
    assert "--simulate" in result.output


def test_demo_simulate_seeds_and_verifies(runner: CliRunner) -> None:
    result = runner.invoke(main, ["demo", "--simulate"])
    assert result.exit_code == 0
    assert "Requests seeded" in result.output
    with Ledger() as ledger:
        assert ledger.request_count() == 60
        assert ledger.verify().ok
    verify_result = runner.invoke(main, ["verify"])
    assert verify_result.exit_code == 0


def test_demo_is_deterministic_and_seeds_fallbacks(runner: CliRunner) -> None:
    result = runner.invoke(main, ["demo", "--simulate", "--requests", "100", "--seed", "7"])
    assert result.exit_code == 0
    with Ledger() as ledger:
        fallbacks = ledger.fallback_count()
        assert fallbacks > 0
        events = ledger._conn.execute(
            "SELECT detection_method, confidence FROM fallback_events"
        ).fetchall()
    assert all(row["detection_method"] == "declared" for row in events)
    assert all(row["confidence"] == 1.0 for row in events)
