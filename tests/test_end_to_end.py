"""End-to-end stranger test: the README/QUICKSTART loop must actually work.

Drives the real CLI (via CliRunner) through demo -> report -> verify -> export ->
verify-export -> rescope, asserting each step succeeds. This is the repo's
definition of done: clone to value in one path.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from switchyard.cli import main


def test_quickstart_loop(runner: CliRunner, tmp_path: Path) -> None:
    # 1. Seed a simulated ledger (no API key).
    demo = runner.invoke(main, ["demo", "--simulate"])
    assert demo.exit_code == 0, demo.output

    # 2. Report shows a fallback rate.
    report = runner.invoke(main, ["report", "--format", "json"])
    assert report.exit_code == 0
    data = json.loads(report.output)
    assert data["total_requests"] == 60
    assert data["fallback_events"] > 0

    # 3. The hash chain verifies.
    verify = runner.invoke(main, ["verify"])
    assert verify.exit_code == 0
    assert "chain verified" in verify.output

    # 4. Export and verify offline.
    out = tmp_path / "evidence.json"
    export = runner.invoke(main, ["export", "-o", str(out)])
    assert export.exit_code == 0
    verify_export = runner.invoke(main, ["verify-export", str(out)])
    assert verify_export.exit_code == 0
    assert "export verified" in verify_export.output

    # 5. CI gate fails on a strict threshold (the demo seeds ~5%).
    check = runner.invoke(main, ["check", "--max-rate", "1%"])
    assert check.exit_code == 1

    # 6. Re-scope suggests a compliant reframe.
    rescope = runner.invoke(main, ["rescope", "exploit this binary"])
    assert rescope.exit_code == 0
    assert "Suggested reframe" in rescope.output


def test_help_lists_full_cli_map(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in (
        "audit",
        "check",
        "config",
        "demo",
        "export",
        "mcp",
        "proxy",
        "report",
        "rescope",
        "templates",
        "verify",
        "verify-export",
    ):
        assert command in result.output


def test_version_matches_package(runner: CliRunner) -> None:
    import switchyard

    result = runner.invoke(main, ["--version"])
    assert switchyard.__version__ in result.output
