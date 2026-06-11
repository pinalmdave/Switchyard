"""Re-scope template library + suggestion engine (F-RSC-01, F-RSC-02).

When a benign security/bio/chem request trips the safeguard classifiers and gets
silently served by Opus 4.8, the fix is usually to *reframe* it so the legitimate
defensive intent is explicit — without changing what you actually need. This module
matches a prompt against built-in templates and suggests a compliant rewrite.

``switchyard rescope "<prompt>"`` matches by task type + keyword/trip signature.
With ``--llm`` it drafts a tailored reframe using the user's own Claude key (Sonnet
by default). It never sends anything on your behalf and never auto-applies — these
are suggestions, recorded in the ledger's ``rescopes`` table so you can track which
templates actually help.

Templates live in ``src/switchyard/templates/*.yaml`` (built-in) and merge with
user templates from ``~/.switchyard/templates/`` (user wins on name collision).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from switchyard.ledger import switchyard_home

REQUIRED_FIELDS = (
    "name",
    "task_type",
    "trip_signature",
    "rewrite_pattern",
    "rationale",
    "example_before",
    "example_after",
)

DEFAULT_LLM_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class Template:
    """One re-scope template (the F-RSC-01 schema)."""

    name: str
    task_type: str
    trip_signature: list[str]
    rewrite_pattern: str
    rationale: str
    example_before: str
    example_after: str
    source: str = "built-in"

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str = "built-in") -> Template:
        missing = [f for f in REQUIRED_FIELDS if f not in data or data[f] in (None, "")]
        if missing:
            name = data.get("name", "<unnamed>")
            raise ValueError(f"template {name!r} missing required fields: {', '.join(missing)}")
        signature = data["trip_signature"]
        if isinstance(signature, str):
            signature = [signature]
        if not isinstance(signature, list) or not all(isinstance(s, str) for s in signature):
            raise ValueError(f"template {data['name']!r}: trip_signature must be a list of strings")
        return cls(
            name=str(data["name"]),
            task_type=str(data["task_type"]),
            trip_signature=[s.lower() for s in signature],
            rewrite_pattern=str(data["rewrite_pattern"]),
            rationale=str(data["rationale"]),
            example_before=str(data["example_before"]),
            example_after=str(data["example_after"]),
            source=source,
        )


@dataclass(frozen=True)
class Match:
    """A scored template match for a prompt."""

    template: Template
    score: float
    matched_signals: list[str]


def builtin_templates_dir() -> Path:
    """Directory of packaged built-in templates."""
    return Path(__file__).parent / "templates"


def user_templates_dir() -> Path:
    """Directory of user templates (``~/.switchyard/templates``)."""
    return switchyard_home() / "templates"


def _load_dir(directory: Path, source: str) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    if not directory.is_dir():
        return templates
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            continue
        items = loaded if isinstance(loaded, list) else [loaded]
        for item in items:
            template = Template.from_dict(item, source=source)
            templates[template.name] = template
    return templates


def load_templates(include_user: bool = True) -> list[Template]:
    """Load built-in templates, then merge user templates over them by name."""
    merged = _load_builtin()
    if include_user:
        merged = {**merged, **_load_dir(user_templates_dir(), source="user")}
    return sorted(merged.values(), key=lambda t: t.name)


@lru_cache(maxsize=1)
def _load_builtin() -> dict[str, Template]:
    return _load_dir(builtin_templates_dir(), source="built-in")


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def match_templates(prompt: str, task_type: str | None = None, limit: int = 3) -> list[Match]:
    """Rank templates for a prompt by trip-signature hits and task-type agreement.

    Scoring: each matched trip-signature phrase contributes to the score, with a
    bonus when the caller-supplied ``task_type`` matches the template's. Templates
    with zero signal hits and no task-type match are dropped.
    """
    prompt_lower = prompt.lower()
    prompt_tokens = _tokens(prompt)
    matches: list[Match] = []
    for template in load_templates():
        matched = [
            phrase
            for phrase in template.trip_signature
            if (" " in phrase and phrase in prompt_lower)
            or (" " not in phrase and phrase in prompt_tokens)
        ]
        signal_score = len(matched)
        task_bonus = 0.0
        if task_type is not None and task_type == template.task_type:
            task_bonus = 2.0
        score = signal_score + task_bonus
        if score <= 0:
            continue
        matches.append(Match(template=template, score=score, matched_signals=matched))
    matches.sort(key=lambda m: (m.score, m.template.name), reverse=True)
    return matches[:limit]


def render_suggestion(template: Template, prompt: str) -> str:
    """Fill a template's rewrite pattern with the original prompt."""
    try:
        return template.rewrite_pattern.format(prompt=prompt, original=prompt)
    except (KeyError, IndexError):
        # A pattern referencing unknown placeholders is still useful verbatim.
        return template.rewrite_pattern


def build_llm_messages(prompt: str, template: Template | None) -> list[dict[str, str]]:
    """Build the messages for the optional ``--llm`` tailored reframe.

    Kept pure (no network) so it is unit-testable; the CLI feeds these to the
    user's own Claude client.
    """
    guidance = (
        f"Use this reframe pattern as a guide:\n{template.rewrite_pattern}\n"
        f"Rationale: {template.rationale}\n"
        if template is not None
        else ""
    )
    content = (
        "You help security professionals reframe legitimate defensive requests so the "
        "benign intent is explicit, keeping the work on the frontier model. Rewrite the "
        "request below. Preserve the actual technical need; make the defensive purpose, "
        "authorization, and use-case clear. Do not water down the request into something "
        "useless. Return only the rewritten request.\n\n"
        f"{guidance}\nOriginal request:\n{prompt}"
    )
    return [{"role": "user", "content": content}]
