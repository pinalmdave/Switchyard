"""Shared fixtures: every test gets an isolated SWITCHYARD_HOME."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point SWITCHYARD_HOME at a temp dir so tests never touch the real ledger."""
    home = tmp_path / "switchyard-home"
    monkeypatch.setenv("SWITCHYARD_HOME", str(home))
    return home


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()
