# Switchyard — Feature Specifications (OSS)

> Companion to [CLAUDE.md](./CLAUDE.md). Every feature has a stable ID for commits,
> issues, and tests (`test_F_DET_01_*`). Build order lives in [SESSIONS.md](./SESSIONS.md).

## 1. Capture surfaces — how traffic reaches Switchyard

### F-CLI-01 · Drop-in SDK wrapper (primary surface)
`from switchyard import Anthropic` — a thin wrapper around the official `anthropic` SDK.
Identical signatures (messages.create, streaming, tools); after each response it runs
detection (F-DET) and writes the ledger (F-LGR). Zero behavior change for the caller.
- Context manager for tagging: `with switchyard.context(engagement="acme-q2", task_type="triage"):`
  — tags attach to every event inside the block.
- Must support sync + async clients and streaming (detection runs on the final message).
- Failure isolation: any Switchyard-internal exception is caught, logged to stderr once,
  and never breaks the user's call. **The wrapper may never raise into user code.**

### F-PRX-01 · Local proxy mode (optional extra: `[proxy]`)
`switchyard proxy --port 4140` starts a localhost-only Starlette/uvicorn passthrough to
`api.anthropic.com`. User sets `ANTHROPIC_BASE_URL=http://127.0.0.1:4140`. Captures traffic
from tools that can't change imports (Claude Code itself, LangChain apps, etc.).
- Streaming (SSE) passthrough byte-for-byte; headers preserved; binds 127.0.0.1 only.
- Adds <5 ms p99 locally (benchmark in tests, generous CI threshold).
- Detection/ledger work happens off the response path in a background task.

## 2. Detection engine

### F-DET-01 · Declared-model detection
Compare `requested model` vs the `model` field in the API response (and any served-by
headers). Mismatch (e.g., requested `claude-fable-5`, served `claude-opus-4-8`) ⇒
fallback event with `detection_method="declared"`, `confidence=1.0`.

### F-DET-02 · Heuristic corroboration
When metadata is absent/ambiguous: rolling per-model baselines (stored in SQLite) of
latency-per-output-token and tokens/sec; a response deviating beyond a configurable z-score
on ≥2 signals ⇒ `detection_method="heuristic"`, confidence 0.5–0.8 (documented formula).
- Heuristics are OFF by default until ≥30 baseline samples exist; never report heuristic
  events as certainties (always show confidence in every surface).
- `docs/DETECTION.md` must document exactly what each method can/can't catch, with the
  false-positive trade-offs. Honesty is a feature.
- LLM behavioral fingerprinting is **out of scope** (paid platform); leave a clean
  `Detector` protocol so it can plug in later.

## 3. Ledger

### F-LGR-01 · Hash-chained local ledger
SQLite at `~/.switchyard/ledger.db`. Tables: `requests`, `fallback_events`, `rescopes`,
`meta`. Every row appended to `requests` carries
`entry_hash = sha256(prev_hash || canonical_json(payload) || sequence)`.
- Privacy modes (`switchyard config set privacy hash|metadata|full`): hash (default) stores
  `prompt_sha256` + counts/timings only; metadata adds model/task/engagement tags; full
  stores bodies (loud warning on enable).
- `switchyard verify` re-walks the chain and reports the first broken link, if any.

### F-LGR-02 · Signed export
`switchyard export --engagement acme-q2 --format json|md` produces a self-contained export:
entries + chain head + an HMAC signature (key generated once into `~/.switchyard/signing.key`).
`switchyard verify-export <file>` validates offline. `docs/LEDGER_FORMAT.md` specifies the
format so third parties can implement independent verifiers.

## 4. Re-scope & reporting

### F-RSC-01 · Template library
`src/switchyard/templates/*.yaml` — built-in, security-vertical-first re-scope templates:
`{name, task_type, trip_signature, rewrite_pattern, rationale, example_before, example_after}`.
User templates in `~/.switchyard/templates/` merge over built-ins. Ship ≥10 quality
security templates (exploit→defensive-impact, payload→detection-engineering, etc.) — these
are content, and content earns stars.

### F-RSC-02 · Suggestion engine
`switchyard rescope "<prompt>"` (or on a ledger event id): matches templates by task_type +
keyword/trip signature; with `--llm` it drafts a tailored reframe using the user's own
Claude key (Sonnet by default) and prints before/after. Never auto-sends anything; suggestions
only. Records the suggestion in `rescopes` so template success can be tracked manually.

### F-RPT-01 · Report
`switchyard report [--since 7d] [--by task_type|engagement|model]` — rich terminal table +
`--format md|json`. Headline numbers: total requests, fallback rate, by-method split,
top tripping task types, estimated retry token cost.

### F-RPT-02 · Audit mode
`switchyard audit` — one command for the funnel moment: confirms capture is working, then
watches live and prints fallback events as they happen (rich live view in the terminal).

### F-RPT-03 · CI gate
`switchyard check --max-rate 0.02 --since 24h` exits non-zero when breached;
`examples/ci_gate.yml` shows the GitHub Actions snippet.

## 5. Claude Code plugin & skill

### F-PLG-01 · Plugin packaging
`plugin/` is an installable Claude Code plugin (marketplace-compatible):
`.claude-plugin/plugin.json` (name `switchyard`, MIT, repo URL), bundling the commands,
the skill, and the MCP server config. Document install in README (add repo as marketplace →
install plugin). Plugin assumes `pip install switchyard-ai` (the skill checks and tells the
user how if missing).

### F-PLG-02 · Skill + slash commands
`plugin/skills/switchyard/SKILL.md` teaches Claude when/how to use the tool (triggers:
"fallback", "did this run on Fable", "safeguard tripped", "audit my Claude usage").
Commands: `/switchyard:audit` (run + interpret F-RPT-02), `/switchyard:report` (run +
summarize F-RPT-01), `/switchyard:rescope <prompt>` (F-RSC-02 + explain the reframe).
Commands shell out to the CLI — no logic duplication.

### F-PLG-03 · MCP server (extra: `[mcp]`)
`switchyard mcp` — stdio MCP server exposing read-only tools: `get_fallback_summary`,
`list_fallback_events`, `suggest_rescope`, `verify_ledger`. Registered by the plugin's
`.mcp.json`. This lets any MCP client (not just Claude Code) query the ledger.

## 6. CLI map (single `switchyard` entry point, click)

```
switchyard audit | report | rescope | check | verify | verify-export | export
           proxy | mcp | config (get/set) | templates (list/show)
```

`--json` on every read command; `--help` examples for each; man-page-quality help text.

## 7. Testing strategy

- Unit: detection math, hash chain (property tests: any mutation breaks verify), template
  matching, privacy-mode storage guarantees (hash mode never writes body bytes — assert).
- Integration: recorded fixture responses (normal, declared-fallback, ambiguous) replayed
  through wrapper and proxy; CLI golden-output tests; plugin command smoke tests.
- Simulation: `switchyard demo --simulate` seeds a fake ledger so README screenshots and
  first-run UX work without real fallbacks — also used by docs and tests.
- No live network in CI. One optional `-m live` suite for local runs with a real key.
