# Security Policy

Switchyard is a tool security professionals rely on for evidence, so we take its own
security seriously.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, use GitHub's private vulnerability reporting:
**Security → Report a vulnerability** on the repository. If that is unavailable to you,
open a minimal public issue asking a maintainer to contact you privately — without
details — and we will follow up.

Please include, where possible:

- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- affected version(s) and environment.

## What to expect

- We aim to acknowledge a report within **5 business days**.
- We will confirm the issue, determine affected versions, and keep you updated on
  remediation progress.
- We support **coordinated disclosure**: we will agree on a disclosure timeline with you
  and credit you in the release notes unless you prefer otherwise.

## Scope

In scope:

- The `switchyard` package and CLI, including the proxy and MCP server.
- The ledger and signed-export format (integrity/tamper-evidence claims).
- Anything that could cause prompt content, API keys, or ledger data to leak off the
  machine, contrary to the privacy guarantees in the README.

Out of scope:

- Vulnerabilities in third-party dependencies (report those upstream; we will bump once
  fixed).
- The planned hosted platform, which does not live in this repository.

## Security posture

- No telemetry, no phone-home. The only outbound network call is the user's own request
  to their configured model provider (or, in proxy mode, the upstream they point at).
- API keys are read from the environment only, never logged, never written to disk.
- The proxy binds loopback addresses only and refuses anything else.
