# Switchyard

> Detect when Claude silently falls back from Fable 5 to Opus 4.8, log every event into a
> local tamper-evident ledger, and keep your work on the frontier model.

**Status: pre-release scaffold.** The full README (quickstart, detection honesty table,
privacy guarantees, plugin install) lands with v0.1.0 — see [SESSIONS.md](./SESSIONS.md).

```bash
pip install switchyard-ai
switchyard demo --simulate   # seed a simulated ledger
switchyard verify            # walk the hash chain
```

**Privacy:** by default Switchyard stores only a SHA-256 of your prompts plus counts and
timings. Your prompts never leave your machine. No telemetry, no phone-home.

MIT licensed.
