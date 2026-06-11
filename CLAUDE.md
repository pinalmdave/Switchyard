# Switchyard — Open-Source Build Guide (root)

> This is the **root index** for the open-source build. Detailed feature specs are in
> [SPEC.md](./SPEC.md); the session-by-session build order is in [SESSIONS.md](./SESSIONS.md).
> Read all three before writing code.

## 1. Project overview & goal

**Switchyard** is an open-source (MIT) Python tool that detects when Claude Fable 5 silently
falls back to Opus 4.8 (safeguard classifiers), logs every event into a local tamper-evident
ledger, suggests compliant re-scopes that keep work on the frontier model, and reports
fallback rates — from the CLI, from CI, and from inside Claude Code as a plugin/skill.

**Why this exists (context for design decisions):** Fable 5 shipped June 9, 2026 with
deliberately over-broad safety classifiers; benign requests in security, bio, and chemistry
work get silently served by Opus 4.8 in up to ~5% of sessions. Anthropic will narrow the
classifiers over the coming months — so the pain is acute *now* and perishable. Strategy:
ship a credible OSS tool fast, earn GitHub stars and trust while the pain is at its maximum,
and keep the durable assets (the ledger format, the template library, the detection corpus)
alive for a later paid platform.

**Goal of this build:** a polished, pip-installable, well-tested repository that a security
engineer can install and get value from in **under 5 minutes**, and that doubles as a
**Claude Code plugin** (skill + slash commands + MCP server). Credibility is the product:
README quality, test coverage, type safety, and honest docs matter as much as features.

## 2. Hard scope boundary — what this repo is NOT

Do **NOT** build any of the following (they are the future paid platform, not the OSS tool):

- ❌ Web UI / dashboard of any kind
- ❌ Azure (or any cloud) deployment, IaC, Bicep, Container Apps, hosted services
- ❌ Multi-tenant anything: orgs, teams, seats, RBAC, auth providers
- ❌ Stripe/billing, peer benchmarks, hosted ledger, trusted-access readiness reports
- ❌ Databases beyond local SQLite; message queues; Redis

If a feature seems to need any of these, it's out of scope — note it in `docs/ROADMAP.md`
under "paid platform" and move on.

## 3. Tech stack & conventions

- **Python 3.11+** (test matrix: 3.11, 3.12, 3.13). Single package: `switchyard`.
- **Packaging:** `pyproject.toml` + hatchling; publish-ready for PyPI as `switchyard-ai`
  (import name `switchyard`); `uv` for dev workflows.
- **Runtime deps (keep minimal — this is a trust-sensitive tool):** `httpx`, `anthropic`,
  `click` (CLI), `pyyaml`, `rich` (terminal reports). Proxy mode: `uvicorn` + `starlette`
  behind an optional extra `switchyard-ai[proxy]`. MCP server behind `switchyard-ai[mcp]`.
- **Storage:** SQLite via stdlib `sqlite3` only. Default location `~/.switchyard/ledger.db`,
  overridable via `SWITCHYARD_HOME`.
- **Quality bar (CI-enforced):** `ruff` (lint + format), `mypy --strict`, `pytest` with
  **≥90% coverage**, no network calls in tests (use recorded fixtures). Conventional commits.
- **Privacy by default:** store `prompt_sha256` + metadata only (hash mode). `metadata` and
  `full` modes are explicit opt-ins. The tool must NEVER transmit prompt content anywhere
  except to the model provider the user already configured. No telemetry, no phone-home —
  state this loudly in the README.
- **Honesty conventions:** detection is probabilistic — every fallback event carries a
  `detection_method` (declared | heuristic) and `confidence`. Never present a heuristic as
  a fact. Docs must say plainly what the tool can and cannot detect.
- **Secrets:** only `ANTHROPIC_API_KEY` (and optional alternates) from env; never log keys;
  never write keys to disk.

## 4. Repository layout

```
switchyard/
├─ CLAUDE.md                    # this file
├─ SPEC.md  ·  SESSIONS.md      # feature specs · build order
├─ LICENSE                      # MIT
├─ README.md                    # the most important file in the repo — see §6
├─ CONTRIBUTING.md  ·  CODE_OF_CONDUCT.md  ·  SECURITY.md
├─ pyproject.toml
├─ src/switchyard/
│  ├─ __init__.py               # public API: Anthropic (wrapped client), audit, __version__
│  ├─ client.py                 # drop-in wrapper around anthropic SDK (F-CLI-01)
│  ├─ proxy.py                  # optional local proxy server (F-PRX-01) [extra: proxy]
│  ├─ detect.py                 # fallback detection engine (F-DET-01/02)
│  ├─ ledger.py                 # hash-chained SQLite ledger (F-LGR-01/02)
│  ├─ rescope.py                # template library + suggestion engine (F-RSC-01/02)
│  ├─ report.py                 # fallback-rate reports, md/json export (F-RPT-01/02)
│  ├─ ci.py                     # CI gate: switchyard check (F-RPT-03)
│  ├─ mcp_server.py             # MCP server exposing tools (F-PLG-03) [extra: mcp]
│  ├─ cli.py                    # click entry point: switchyard …
│  └─ templates/                # built-in re-scope templates (YAML, security-first)
├─ plugin/                      # the Claude Code plugin (F-PLG-01/02) — see SPEC §5
│  ├─ .claude-plugin/plugin.json
│  ├─ commands/                 # /switchyard:audit · :report · :rescope
│  └─ skills/switchyard/SKILL.md
├─ examples/                    # runnable scripts: sdk_wrapper.py, proxy_mode.py, ci_gate.yml
├─ tests/                       # unit + integration (recorded fixtures in tests/fixtures/)
├─ docs/                        # QUICKSTART.md · DETECTION.md · LEDGER_FORMAT.md · ROADMAP.md
└─ .github/                     # workflows/ci.yml · release.yml · ISSUE_TEMPLATE/ · FUNDING.yml
```

## 5. The product in one paragraph (keep every feature pointed at this)

A pentest firm runs `pip install switchyard-ai`, swaps one import (or starts the local
proxy), and keeps working. Switchyard watches every response: when Fable 5 silently hands
the request to Opus 4.8, the event lands in a local hash-chained ledger with method +
confidence; `switchyard report` shows which task types trip and how often;
`switchyard rescope` suggests a compliant reframe that stays on the frontier model;
`switchyard check --max-rate 2%` gates CI; and inside Claude Code, `/switchyard:audit`
does all of it conversationally. Nothing leaves the machine.

## 6. Credibility checklist (this IS the go-to-market)

The README must contain, in order: one-sentence problem statement → 30-second
quickstart (3 commands) → terminal-output screenshot/GIF placeholder → "what it detects
and how (honestly)" table → privacy guarantees ("your prompts never leave your machine") →
Claude Code plugin install → roadmap link → MIT badge, CI badge, coverage badge, PyPI badge.
Every public function has a docstring; `docs/DETECTION.md` explains the detection methods
and their limits; `docs/LEDGER_FORMAT.md` specifies the hash chain so third parties can
verify exports independently; CHANGELOG kept from v0.1.0; issue + PR templates;
`SECURITY.md` with a disclosure policy. Tag `v0.1.0` only when the full credibility
checklist passes.

## 7. Build order & definition of done

Follow [SESSIONS.md](./SESSIONS.md): 8 sessions in 4 waves; sessions inside a wave are
parallel-safe (disjoint files). **Definition of done for every session:** code + tests
green in CI + docs updated + the session's demo script runs clean. Definition of done for
the repo: a stranger can go from `git clone` to a detected (simulated) fallback event and
a verified ledger export in under 5 minutes using only the README.

## 8. Deferred to the paid platform (record, don't build)

Hosted multi-tenant ledger & team RBAC · web dashboard · peer benchmarks across orgs ·
LLM-based behavioral fingerprinting at scale · trusted-access readiness reports & evidence
packs · alternate-provider auto-rerouting with policy engine · Slack/Teams alerting ·
SOC 2 evidence tooling. Keep `docs/ROADMAP.md` listing these under "Switchyard Cloud
(planned)" — it signals seriousness without promising dates.
