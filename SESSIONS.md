# Switchyard — Build Sessions (OSS)

> Session-grouped plan for parallel Claude Code agents. Each session is one coherent
> bundle: developable, testable, and "deployable" (= merged to main with CI green) in a
> single sitting. Sessions inside a wave touch disjoint files and can run as parallel
> agents. Feature IDs refer to [SPEC.md](./SPEC.md).

## Wave 1 — Skeleton (2 parallel sessions)

### Session S1 · Repo scaffold + ledger core  — `pyproject`, `ledger.py`, `cli.py` (stub), CI
1. `pyproject.toml` (hatchling, extras `[proxy]`, `[mcp]`, dev group), `src/` layout, MIT
   LICENSE, ruff + mypy --strict configs, `.github/workflows/ci.yml` (3.11–3.13 matrix,
   lint, type, test+coverage gate ≥90%).
2. `ledger.py`: schema, hash chain, privacy modes, `verify` (F-LGR-01).
3. `cli.py`: click skeleton with `config`, `verify`, `demo --simulate` seeding a fake ledger.
- **Test gate:** chain property tests (any mutation breaks verify); privacy-mode assertions;
  CI green on the matrix.
- **Demo:** `switchyard demo --simulate && switchyard verify` ✓.

### Session S2 · Detection engine — `detect.py` + fixtures
1. `Detector` protocol; declared-model detector (F-DET-01); heuristic detector with
   baselines + z-score formula (F-DET-02), off until 30 samples.
2. Recorded fixtures: normal / declared-fallback / ambiguous response sets in `tests/fixtures/`.
3. `docs/DETECTION.md` — honest capability/limits doc, written alongside the code.
- **Test gate:** precision 1.0 on declared fixtures; heuristic confidence bounds respected;
  no event without method+confidence.
- **Demo:** pytest detection suite + doc renders.

## Wave 2 — Capture surfaces (2 parallel sessions, after Wave 1)

### Session S3 · SDK wrapper — `client.py`
Drop-in `switchyard.Anthropic` (sync/async/streaming), tagging context manager, failure
isolation (never raises into user code), wires detect→ledger (F-CLI-01).
- **Test gate:** signature-parity tests vs `anthropic` SDK; fixture replay produces correct
  ledger rows; injected internal errors don't propagate.
- **Demo:** `examples/sdk_wrapper.py` against fixtures.

### Session S4 · Proxy mode — `proxy.py` (extra `[proxy]`)
Localhost SSE passthrough, background detection task, latency benchmark (F-PRX-01).
- **Test gate:** byte-for-byte streaming parity on fixtures; 127.0.0.1 bind enforced;
  latency benchmark under threshold.
- **Demo:** `examples/proxy_mode.py`; Claude Code pointed at the proxy captures a session.

## Wave 3 — Value layer (3 parallel sessions, after Wave 2)

### Session S5 · Reports + CI gate — `report.py`, `ci.py`
`report` (rich tables, md/json), `audit` live view, `check` exit codes (F-RPT-01/02/03),
`examples/ci_gate.yml`.
- **Test gate:** golden-output CLI tests; `check` exit-code matrix.
- **Demo:** simulated ledger → `switchyard report` screenshot for the README.

### Session S6 · Re-scope — `rescope.py`, `templates/`
Template schema + ≥10 security templates, matcher, `--llm` tailored reframe via user's key,
suggestions recorded (F-RSC-01/02).
- **Test gate:** matcher unit tests; `--llm` path mocked; template YAML schema validated in CI.
- **Demo:** `switchyard rescope "exploit this binary"` prints a defensive reframe.

### Session S7 · Signed export — extend `ledger.py`
HMAC-signed export + offline `verify-export`, `docs/LEDGER_FORMAT.md` spec (F-LGR-02).
- **Test gate:** tampered export fails verification; spec doc matches implementation
  (doctest the examples).
- **Demo:** export → verify round-trip.

## Wave 4 — Distribution (2 sessions: S8 parallel-safe, S9 last)

### Session S8 · Claude Code plugin + MCP — `plugin/`, `mcp_server.py`
Plugin manifest, skill, 3 slash commands, MCP server + `.mcp.json` (F-PLG-01/02/03).
- **Test gate:** plugin JSON validates; commands shell out correctly (smoke); MCP tools
  return correct shapes on a simulated ledger.
- **Demo:** install plugin into Claude Code → `/switchyard:report` works.

### Session S9 · Credibility pass + v0.1.0 (final, single agent)
README per CLAUDE.md §6 (badges, quickstart, honesty table, privacy guarantees),
CONTRIBUTING/CODE_OF_CONDUCT/SECURITY, issue/PR templates, CHANGELOG, `release.yml`
(tag → build → PyPI publish via trusted publishing), `docs/ROADMAP.md` with the
"Switchyard Cloud (planned)" section, end-to-end stranger test: clone → value in <5 min.
- **Exit:** tag `v0.1.0`; repo public; quickstart timed-run clean.

## Cross-session rules
- The ledger schema (S1) and the `Detector` protocol (S2) are the only shared contracts —
  freeze them at the end of Wave 1; later sessions may extend, not break.
- Every session updates docs it touches; no session leaves a TODO without a GitHub issue.
- Conventional commits; one PR per session; squash-merge with the session ID in the title.
