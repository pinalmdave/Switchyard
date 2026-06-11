# Roadmap

Switchyard (this repo) is the open-source core: a local CLI, SDK wrapper, proxy, ledger,
detection engine, re-scope library, and Claude Code plugin. It is MIT-licensed and works
entirely on your machine.

## Open-source core — near-term

These belong in this repo and are fair game for contributions:

- Additional re-scope templates (more verticals, community-contributed).
- More capture surfaces (e.g. an OpenTelemetry exporter that reads the ledger).
- Richer heuristic signals while keeping the honesty contract (method + confidence on
  every event).
- Independent third-party verifier implementations of the ledger/export format
  (see [LEDGER_FORMAT.md](./LEDGER_FORMAT.md)).
- Quality-of-life: shell completions, a `switchyard doctor` setup check.

## Switchyard Cloud (planned)

The following are **out of scope** for the open-source tool and are noted here only to
signal direction. They are not promises and carry no dates.

- Hosted, multi-tenant ledger with team RBAC, orgs, and seats.
- Web dashboard and historical trend analytics.
- Peer benchmarks across organizations (how does your fallback rate compare?).
- LLM-based behavioral fingerprinting at corpus scale (the OSS `Detector` protocol is the
  plug-in point, but the scale lives in the platform).
- Trusted-access readiness reports and evidence packs.
- Alternate-provider auto-rerouting with a policy engine.
- Slack/Teams alerting and SOC 2 evidence tooling.

The durable open-source assets — the ledger format, the template library, and the
detection corpus — are designed to stay useful on their own regardless of the platform.
