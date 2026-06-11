# Changelog

All notable changes to Switchyard are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-11

First public release.

### Added

- **Detection engine** (`detect.py`): declared-model detection (confidence 1.0, with
  snapshot-alias normalization) and a heuristic z-score detector over rolling per-model
  timing baselines (off until 30 samples, confidence bounded to 0.5–0.8). Every event
  carries a detection method and confidence. A `Detector` protocol leaves room for future
  detectors.
- **Hash-chained ledger** (`ledger.py`): append-only SQLite with privacy modes
  (`hash` default / `metadata` / `full`); `switchyard verify` re-walks the chain and
  reports the first broken link.
- **Signed export** (`export.py`): HMAC-SHA256 self-contained exports that
  `switchyard verify-export` checks offline, committing to the full chain head. Format
  specified in `docs/LEDGER_FORMAT.md`.
- **Drop-in SDK wrapper** (`client.py`): `from switchyard import Anthropic` with sync,
  async, and streaming support; a tagging context manager; and hard failure isolation —
  the wrapper never raises into user code.
- **Local proxy** (`proxy.py`, extra `[proxy]`): loopback-only, byte-for-byte SSE
  passthrough with off-path detection.
- **Reports & CI gate** (`report.py`, `ci.py`): `switchyard report` (table/md/json),
  live `switchyard audit`, and `switchyard check --max-rate` for pipelines.
- **Re-scope library** (`rescope.py`, `templates/`): 12 security-first templates, a
  matcher, and an optional `--llm` tailored reframe using your own key. Suggestions only.
- **Claude Code plugin** (`plugin/`) and a read-only **MCP server** (`mcp_server.py`,
  extra `[mcp]`): skill, three slash commands, and four read-only tools over the ledger.
- Docs: `README`, `QUICKSTART`, `DETECTION`, `LEDGER_FORMAT`, `ROADMAP`; runnable
  examples for the SDK wrapper, proxy mode, and CI gate.

### Security

- No telemetry and no phone-home. The only network call is your own request to the model
  provider you configured. Prompts never leave your machine; `hash` mode stores only a
  SHA-256 of each prompt. API keys are read from the environment, never logged, never
  written to disk.

[Unreleased]: https://github.com/pinalmdave/switchyard/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pinalmdave/switchyard/releases/tag/v0.1.0
