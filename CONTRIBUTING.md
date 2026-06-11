# Contributing to Switchyard

Thanks for helping make Claude model fallbacks visible and auditable. Switchyard is a
trust-sensitive tool, so the bar for contributions is high — but the workflow is simple.

## Ground rules

- **Scope.** This repo is the open-source core (local CLI, SDK wrapper, proxy, ledger,
  detection, re-scope, plugin). Hosted/multi-tenant/dashboard features are out of scope —
  see [docs/ROADMAP.md](./docs/ROADMAP.md). If unsure, open an issue first.
- **Privacy is non-negotiable.** No telemetry, no phone-home, no transmitting prompt
  content anywhere except the model provider the user already configured. PRs that weaken
  the privacy guarantees will not be merged.
- **Honesty about detection.** Every fallback event must carry a `detection_method` and
  `confidence`. Never present a heuristic as a fact, in code or docs.

## Dev setup

Switchyard uses [uv](https://docs.astral.sh/uv/) and supports Python 3.11–3.13.

```bash
uv sync --all-extras --dev
uv run pytest -m "not live"
```

## Before you open a PR

CI enforces all of the following — run them locally first:

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
uv run mypy                    # strict type checking
uv run pytest -m "not live"    # tests, coverage gate >= 90%, no network
```

- **Tests required.** New behavior needs tests. Detection math, the hash chain, and
  privacy guarantees are property-tested; match that rigor.
- **No network in tests.** Use recorded fixtures (`tests/fixtures/`) and mock transports.
  Live tests go behind the `live` marker and run only locally with a real key.
- **Conventional commits.** e.g. `feat(detect): …`, `fix(ledger): …`, `docs: …`.
- **Docs travel with code.** Update the relevant `docs/` file in the same PR; never leave
  a `TODO` without a tracking issue.

## Adding a re-scope template

Drop a YAML file in `src/switchyard/templates/` following the schema in any existing
template (all seven fields are required). The schema is validated in CI. Keep examples
genuinely defensive — the point is to make legitimate intent explicit, not to launder
disallowed requests.

## Reporting bugs / requesting features

Use the issue templates. For anything security-sensitive, follow
[SECURITY.md](./SECURITY.md) instead of opening a public issue.
