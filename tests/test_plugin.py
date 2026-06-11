"""Plugin + MCP tests (F-PLG-01/02/03): manifest validity, command/skill wiring,
MCP tool shapes over a simulated ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from helpers import FABLE, OPUS
from switchyard.cli import main
from switchyard.ledger import Ledger, RequestRecord

PLUGIN = Path(__file__).resolve().parent.parent / "plugin"


def seed() -> None:
    with Ledger() as ledger:
        ledger.set_privacy_mode("metadata")  # so task_type tags are stored
        for i in range(10):
            entry = ledger.append_request(
                RequestRecord(
                    requested_model=FABLE,
                    served_model=OPUS if i < 2 else FABLE,
                    prompt=f"req {i}",
                    task_type="exploit-analysis",
                )
            )
            if i < 2:
                ledger.record_fallback(entry.sequence, "declared", 1.0, FABLE, OPUS)


# -- plugin manifest (F-PLG-01) ---------------------------------------------------


def test_plugin_json_is_valid() -> None:
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "switchyard"
    assert manifest["license"] == "MIT"
    assert manifest["repository"]
    assert manifest["version"]


def test_mcp_json_is_valid() -> None:
    config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["switchyard"]
    assert server["command"] == "switchyard"
    assert server["args"] == ["mcp"]


def test_three_slash_commands_present() -> None:
    commands = {p.stem for p in (PLUGIN / "commands").glob("*.md")}
    assert {"audit", "report", "rescope"} <= commands


@pytest.mark.parametrize("command", ["audit", "report", "rescope"])
def test_command_has_frontmatter_and_invokes_cli(command: str) -> None:
    text = (PLUGIN / "commands" / f"{command}.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "description:" in text
    assert "switchyard" in text  # shells out to the CLI, no logic duplication


def test_skill_present_with_triggers() -> None:
    skill = (PLUGIN / "skills" / "switchyard" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---")
    assert "name: switchyard" in skill
    for trigger in ("fallback", "Fable", "safeguard", "audit my Claude usage"):
        assert trigger in skill


# -- MCP tools (F-PLG-03) ---------------------------------------------------------


def test_mcp_get_fallback_summary_shape() -> None:
    seed()
    from switchyard.mcp_server import get_fallback_summary

    summary = get_fallback_summary()
    assert summary["total_requests"] == 10
    assert summary["fallback_events"] == 2
    assert summary["fallback_rate"] == 0.2
    assert summary["by_method"]["declared"]["events"] == 2
    assert summary["groups"][0]["key"] == "exploit-analysis"


def test_mcp_list_fallback_events_shape() -> None:
    seed()
    from switchyard.mcp_server import list_fallback_events

    result = list_fallback_events(limit=5)
    assert result["count"] == 2
    event = result["events"][0]
    assert set(event) == {
        "id",
        "created_at",
        "detection_method",
        "confidence",
        "requested_model",
        "served_model",
    }
    assert event["detection_method"] == "declared"
    assert event["confidence"] == 1.0


def test_mcp_list_events_limit_clamped() -> None:
    seed()
    from switchyard.mcp_server import list_fallback_events

    assert list_fallback_events(limit=0)["count"] <= 2  # clamped to >=1, only 2 exist
    assert list_fallback_events(limit=99999)["count"] == 2


def test_mcp_suggest_rescope_is_read_only() -> None:
    from switchyard.mcp_server import suggest_rescope

    result = suggest_rescope("exploit this binary")
    assert result["matches"][0]["name"] == "exploit-to-defensive-impact"
    assert result["suggestion"]
    with Ledger() as ledger:  # MCP suggest must NOT record (unlike the CLI)
        assert ledger._conn.execute("SELECT COUNT(*) AS n FROM rescopes").fetchone()["n"] == 0


def test_mcp_suggest_rescope_no_match() -> None:
    from switchyard.mcp_server import suggest_rescope

    result = suggest_rescope("write a haiku about the sea")
    assert result["matches"] == []
    assert result["suggestion"] is None


def test_mcp_verify_ledger() -> None:
    seed()
    from switchyard.mcp_server import verify_ledger

    result = verify_ledger()
    assert result["ok"] is True
    assert result["entries_checked"] == 10


def test_mcp_build_server_registers_four_tools() -> None:
    from switchyard.mcp_server import build_server

    server = build_server()
    import anyio

    tools = anyio.run(server.list_tools)
    names = {t.name for t in tools}
    assert names == {
        "get_fallback_summary",
        "list_fallback_events",
        "suggest_rescope",
        "verify_ledger",
    }


# -- CLI mcp entry ----------------------------------------------------------------


def test_cli_mcp_command_exists(runner: CliRunner) -> None:
    result = runner.invoke(main, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "MCP server" in result.output
