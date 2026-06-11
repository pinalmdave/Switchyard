"""Re-scope tests (F-RSC-01/02): template schema, matcher, --llm path mocked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from switchyard.cli import main
from switchyard.ledger import Ledger
from switchyard.rescope import (
    REQUIRED_FIELDS,
    Template,
    build_llm_messages,
    builtin_templates_dir,
    load_templates,
    match_templates,
    render_suggestion,
    user_templates_dir,
)


@pytest.fixture(autouse=True)
def clear_template_cache() -> None:
    from switchyard.rescope import _load_builtin

    _load_builtin.cache_clear()


# -- template library schema (validated in CI) -----------------------------------


def test_at_least_ten_builtin_templates() -> None:
    templates = [t for t in load_templates() if t.source == "built-in"]
    assert len(templates) >= 10


def test_every_builtin_yaml_validates() -> None:
    for path in builtin_templates_dir().glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        for item in items:
            template = Template.from_dict(item)  # raises on any schema violation
            for field in REQUIRED_FIELDS:
                assert getattr(template, field)


def test_builtin_template_names_are_unique() -> None:
    names = [t.name for t in load_templates()]
    assert len(names) == len(set(names))


def test_security_verticals_are_covered() -> None:
    task_types = {t.task_type for t in load_templates()}
    for vertical in ("exploit-analysis", "detection-engineering", "malware-triage"):
        assert vertical in task_types


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_from_dict_rejects_missing_field(missing: str) -> None:
    data: dict[str, Any] = {
        "name": "x",
        "task_type": "t",
        "trip_signature": ["sig"],
        "rewrite_pattern": "p {prompt}",
        "rationale": "r",
        "example_before": "b",
        "example_after": "a",
    }
    del data[missing]
    with pytest.raises(ValueError, match="missing required fields"):
        Template.from_dict(data)


def test_from_dict_normalizes_string_signature() -> None:
    template = Template.from_dict(
        {
            "name": "x",
            "task_type": "t",
            "trip_signature": "Exploit",
            "rewrite_pattern": "p",
            "rationale": "r",
            "example_before": "b",
            "example_after": "a",
        }
    )
    assert template.trip_signature == ["exploit"]


# -- matcher ----------------------------------------------------------------------


def test_match_exploit_prompt() -> None:
    matches = match_templates("Please exploit this binary for me")
    assert matches
    assert matches[0].template.name == "exploit-to-defensive-impact"
    assert "exploit" in matches[0].matched_signals


def test_task_type_biases_ranking() -> None:
    # "crack" hits both password-cracking and binary-RE signatures; the task
    # type should tip the ranking toward the credential-audit template.
    matches = match_templates("how do I crack this", task_type="credential-audit")
    assert matches[0].template.task_type == "credential-audit"


def test_no_match_returns_empty() -> None:
    assert match_templates("write me a haiku about clouds") == []


def test_multiword_signature_requires_phrase() -> None:
    # "command and control" is a phrase signature; the individual word "command"
    # in an unrelated sentence must not trigger it.
    names = {m.template.name for m in match_templates("run this shell command for me")}
    assert "c2-to-network-detection" not in names


def test_render_suggestion_inserts_prompt() -> None:
    template = load_templates()[0]
    rendered = render_suggestion(template, "MY-PROMPT-TEXT")
    if "{prompt}" in template.rewrite_pattern or "{original}" in template.rewrite_pattern:
        assert "MY-PROMPT-TEXT" in rendered


def test_render_suggestion_tolerates_unknown_placeholder() -> None:
    template = Template.from_dict(
        {
            "name": "x",
            "task_type": "t",
            "trip_signature": ["sig"],
            "rewrite_pattern": "literal {unknown} braces",
            "rationale": "r",
            "example_before": "b",
            "example_after": "a",
        }
    )
    assert render_suggestion(template, "p") == "literal {unknown} braces"


# -- user templates merge ----------------------------------------------------------


def test_user_template_overrides_builtin_by_name(isolated_home: Path) -> None:
    from switchyard.rescope import _load_builtin

    _load_builtin.cache_clear()
    user_dir = user_templates_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "custom.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "exploit-to-defensive-impact",  # same name as a built-in
                "task_type": "custom",
                "trip_signature": ["exploit"],
                "rewrite_pattern": "CUSTOM {prompt}",
                "rationale": "mine",
                "example_before": "b",
                "example_after": "a",
            }
        ),
        encoding="utf-8",
    )
    by_name = {t.name: t for t in load_templates()}
    overridden = by_name["exploit-to-defensive-impact"]
    assert overridden.source == "user"
    assert overridden.task_type == "custom"


# -- LLM messages (no network) -----------------------------------------------------


def test_build_llm_messages_includes_prompt_and_template() -> None:
    template = load_templates()[0]
    messages = build_llm_messages("crack this", template)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "crack this" in messages[0]["content"]
    assert template.rewrite_pattern.strip()[:20] in messages[0]["content"]


def test_build_llm_messages_without_template() -> None:
    messages = build_llm_messages("crack this", None)
    assert "crack this" in messages[0]["content"]


# -- CLI ----------------------------------------------------------------------------


def test_cli_rescope_matches_and_records(runner: CliRunner) -> None:
    result = runner.invoke(main, ["rescope", "exploit this binary"])
    assert result.exit_code == 0
    assert "exploit-to-defensive-impact" in result.output
    assert "Suggested reframe" in result.output
    with Ledger() as ledger:
        row = ledger._conn.execute("SELECT * FROM rescopes").fetchone()
    assert row["template_name"] == "exploit-to-defensive-impact"
    assert row["original_sha256"] is not None


def test_cli_rescope_json(runner: CliRunner) -> None:
    result = runner.invoke(main, ["rescope", "crack these hashes", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["matches"]
    assert payload["suggestion"]


def test_cli_rescope_no_match(runner: CliRunner) -> None:
    result = runner.invoke(main, ["rescope", "haiku about the sea"])
    assert result.exit_code == 0
    assert "No template matched" in result.output


def test_cli_rescope_llm_path_mocked(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    import switchyard.cli as cli_module

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_reframe(prompt: str, template: object, model: str) -> str:
        assert model == "claude-sonnet-4-6"
        return "TAILORED REFRAME OUTPUT"

    monkeypatch.setattr(cli_module, "_llm_reframe", fake_reframe)
    result = runner.invoke(main, ["rescope", "exploit this binary", "--llm"])
    assert result.exit_code == 0
    assert "TAILORED REFRAME OUTPUT" in result.output
    with Ledger() as ledger:
        assert ledger._conn.execute("SELECT COUNT(*) AS n FROM rescopes").fetchone()["n"] == 1


def test_cli_rescope_llm_requires_key(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(main, ["rescope", "exploit this binary", "--llm"])
    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_cli_templates_list(runner: CliRunner) -> None:
    result = runner.invoke(main, ["templates", "list"])
    assert result.exit_code == 0
    assert "exploit-to-defensive-impact" in result.output


def test_cli_templates_list_json(runner: CliRunner) -> None:
    result = runner.invoke(main, ["templates", "list", "--json"])
    assert result.exit_code == 0
    names = {t["name"] for t in json.loads(result.output)}
    assert "malware-to-triage" in names


def test_cli_templates_show(runner: CliRunner) -> None:
    result = runner.invoke(main, ["templates", "show", "malware-to-triage"])
    assert result.exit_code == 0
    assert "rewrite_pattern" in result.output
    assert "example_before" in result.output


def test_cli_templates_show_unknown(runner: CliRunner) -> None:
    result = runner.invoke(main, ["templates", "show", "nope"])
    assert result.exit_code != 0
    assert "no template named" in result.output
