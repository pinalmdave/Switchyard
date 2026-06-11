# Switchyard

[![CI](https://github.com/pinalmdave/Switchyard/actions/workflows/ci.yml/badge.svg)](https://github.com/pinalmdave/Switchyard/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/pinalmdave/Switchyard/branch/main/graph/badge.svg)](https://codecov.io/gh/pinalmdave/Switchyard)
[![PyPI](https://img.shields.io/pypi/v/switchyard-ai)](https://pypi.org/project/switchyard-ai/)
[![Python](https://img.shields.io/pypi/pyversions/switchyard-ai)](https://pypi.org/project/switchyard-ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

**Switchyard detects when Claude silently falls back from Fable 5 to Opus 4.8, logs every
event into a local tamper-evident ledger, and suggests compliant re-scopes that keep your
work on the frontier model.**

Fable 5 shipped with deliberately over-broad safety classifiers: benign requests in
security, bio, and chemistry work get silently served by Opus 4.8 in a small but real share
of sessions. If you do authorized security work, you deserve to know when it happens — and
to have evidence. Switchyard gives you that, entirely on your own machine.

## 30-second quickstart

```bash
pip install switchyard-ai
switchyard demo --simulate    # seed a simulated ledger — no API key needed
switchyard report             # see the fallback rate
switchyard verify             # walk the tamper-evident hash chain
```

Then capture your real traffic by changing one import:

```python
from switchyard import Anthropic          # was: from anthropic import Anthropic

client = Anthropic()
client.messages.create(model="claude-fable-5", max_tokens=1024, messages=[...])
# every call is now audited locally; your code is otherwise unchanged
```

…or capture any tool (Claude Code itself, LangChain, …) with the proxy, no code change:

```bash
switchyard proxy --port 4140
export ANTHROPIC_BASE_URL=http://127.0.0.1:4140
```

## What it looks like

```
$ switchyard report
        Switchyard fallback report
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Metric                  ┃  Value ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ Total requests          │     60 │
│ Fallback events         │      3 │
│ Fallback rate           │  5.00% │
│ Privacy mode            │   hash │
│ Est. retry token cost   │  9,967 │
└─────────────────────────┴────────┘
```

> Screenshot/GIF placeholder — replace with an asciinema cast before launch.

## What it detects, and how (honestly)

Detection is **probabilistic**, and Switchyard never hides that. Every event carries a
detection method and a confidence, shown on every surface.

| Method      | What it catches                                            | Confidence | Limits |
| ----------- | ---------------------------------------------------------- | ---------- | ------ |
| `declared`  | The API response itself names a different serving model     | `1.0`      | Can't catch a server that misreports; gives no verdict when nothing is declared |
| `heuristic` | Undeclared responses whose timing deviates from baseline    | `0.5–0.8`  | Off until 30 baseline samples; timing has many non-fallback causes; can't say which model served |

Snapshot aliases (`claude-fable-5-20260609`) are normalized, so they're never reported as
fallbacks. Full details and trade-offs: [docs/DETECTION.md](./docs/DETECTION.md).

## Privacy — your prompts never leave your machine

- **Local only.** The ledger is a SQLite file at `~/.switchyard/ledger.db`
  (override with `SWITCHYARD_HOME`). Switchyard makes **no telemetry calls and never
  phones home.** The only network traffic is your own request to the model provider you
  already configured.
- **Hash mode by default.** Switchyard stores a SHA-256 of each prompt plus counts and
  timings — not the prompt itself. `metadata` mode adds engagement/task tags; `full` mode
  stores bodies and prints a loud warning when you enable it.
- **You hold the keys.** Only `ANTHROPIC_API_KEY` is read, from the environment. Keys are
  never logged and never written to disk.

## Tamper-evident ledger

Every request is appended to a hash chain (`entry_hash = sha256(prev_hash ‖ payload ‖ seq)`).
`switchyard verify` re-walks it and reports the first broken link. `switchyard export`
produces an HMAC-signed, self-contained document that `switchyard verify-export` checks
**offline** — the format is fully specified in
[docs/LEDGER_FORMAT.md](./docs/LEDGER_FORMAT.md) so third parties can write independent
verifiers.

## Keep work on the frontier model

```bash
$ switchyard rescope "exploit this binary"
exploit-to-defensive-impact (task=exploit-analysis)
  Suggested reframe (not sent anywhere):
  I'm assessing the defensive impact of a known vulnerability as part of an
  authorized security engagement. Explain how the following issue could be abused…
```

12 built-in, security-first templates turn requests that trip the classifiers into
compliant reframes that keep the legitimate intent. Add `--llm` to draft a tailored
rewrite with your own key. Suggestions only — nothing is ever auto-sent.

## Gate CI on the fallback rate

```bash
switchyard check --max-rate 2% --since 24h   # exits non-zero if breached
```

See [examples/ci_gate.yml](./examples/ci_gate.yml) for a ready-to-copy GitHub Actions step.

## Claude Code plugin

Switchyard ships as a Claude Code plugin — a skill, three slash commands
(`/switchyard:audit`, `/switchyard:report`, `/switchyard:rescope`), and a read-only MCP
server. Add this repo as a plugin marketplace, then install `switchyard`; the skill checks
that the CLI is installed and tells you how if not. The plugin assumes
`pip install switchyard-ai`.

## CLI map

```
switchyard audit | report | rescope | check | verify | verify-export | export
           proxy | mcp | config (get/set) | templates (list/show) | demo
```

`--json` is available on every read command; `--help` carries examples for each.

## Roadmap & contributing

This is the open-source core. The hosted, multi-tenant platform is tracked separately in
[docs/ROADMAP.md](./docs/ROADMAP.md) under "Switchyard Cloud (planned)". Contributions
welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md), [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md),
and our disclosure policy in [SECURITY.md](./SECURITY.md).

## License

MIT — see [LICENSE](./LICENSE).
