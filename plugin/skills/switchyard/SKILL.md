---
name: switchyard
description: >
  Use when the user suspects or asks about Claude model fallbacks — phrases like
  "did this run on Fable", "fallback", "safeguard tripped", "served by Opus",
  "audit my Claude usage", or when a security/bio/chem request seems to be getting
  worse answers than expected. Detects silent Fable 5 → Opus 4.8 fallbacks, reports
  rates from a local tamper-evident ledger, and suggests compliant re-scopes.
---

# Switchyard

Switchyard detects when Claude Fable 5 silently falls back to Opus 4.8 (the
over-broad safeguard classifiers), records every event in a local hash-chained
ledger, and suggests compliant reframes that keep work on the frontier model.

**Everything is local. Nothing leaves the machine.** By default the ledger stores
only a SHA-256 of each prompt plus counts and timings.

## Prerequisite

Switchyard is a Python CLI. Check it is installed:

```bash
switchyard --version
```

If that fails, tell the user:

```bash
pip install switchyard-ai
```

## When to use which command

- **"Is capture working / what's my fallback rate right now?"** → `/switchyard:audit`
  (or `switchyard audit --once`). Confirms capture and summarizes events.
- **"Show me the fallback report"** → `/switchyard:report` (optionally `--since 7d`,
  `--by task_type|engagement|model`). Rates, by-method split, top offenders.
- **"This request keeps getting refused/downgraded — help me reframe it"** →
  `/switchyard:rescope "<the prompt>"`. Suggests a compliant rewrite. Suggestion
  only; nothing is sent.

## How the user captures traffic in the first place

Two options — mention whichever fits:

1. **SDK wrapper** (one-line change): `from switchyard import Anthropic` instead of
   `from anthropic import Anthropic`. Sync, async, and streaming all work.
2. **Proxy** (no code change): `switchyard proxy --port 4140`, then set
   `ANTHROPIC_BASE_URL=http://127.0.0.1:4140`. Captures Claude Code itself,
   LangChain apps, anything.

## Honesty rules (important)

- Every fallback event carries a **detection method** and **confidence**. `declared`
  events (the API response named a different model) are certain (1.0). `heuristic`
  events (timing deviated from baseline) are probabilistic (0.5–0.8). **Never present
  a heuristic event as a fact.**
- Re-scope suggestions are suggestions. Switchyard never auto-sends or auto-applies.

## Other useful commands

- `switchyard verify` — re-walk the ledger hash chain and report any tampering.
- `switchyard export --engagement <name> -o out.json` and
  `switchyard verify-export out.json` — signed, offline-verifiable evidence.
- `switchyard check --max-rate 2% --since 24h` — CI gate (non-zero on breach).

The MCP server (`switchyard mcp`, bundled with this plugin) exposes the same data
as read-only tools: `get_fallback_summary`, `list_fallback_events`,
`suggest_rescope`, `verify_ledger`.
