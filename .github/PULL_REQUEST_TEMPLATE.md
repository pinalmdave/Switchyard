<!-- Conventional-commit-style title, e.g. feat(detect): add served-by header check -->

## What & why

<!-- What does this change, and what problem does it solve? -->

## Checklist

- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run mypy` passes (strict)
- [ ] `uv run pytest -m "not live"` passes, coverage stays ≥ 90%
- [ ] Tests added/updated for the change (no network in tests)
- [ ] Docs updated in the same PR (if behavior or CLI changed)
- [ ] Preserves the privacy guarantees (no telemetry; prompts never leave the machine)
- [ ] Every fallback event still carries a detection method + confidence

## Notes for reviewers

<!-- Anything that needs context: trade-offs, follow-ups, out-of-scope items. -->
