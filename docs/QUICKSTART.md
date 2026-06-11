# Quickstart

Goal: from a clean machine to a detected (simulated) fallback and a verified ledger export
in under five minutes. No API key required for the simulated path.

## 1. Install

```bash
pip install switchyard-ai
switchyard --version
```

## 2. See it work without an API key

```bash
switchyard demo --simulate     # seeds ~60 requests with a realistic ~5% fallback rate
switchyard report              # fallback rate, by-method split, top task types
switchyard verify              # re-walk the hash chain: "OK: chain verified"
```

## 3. Produce verifiable evidence

```bash
switchyard export -o evidence.json
switchyard verify-export evidence.json    # "OK: export verified"
```

`evidence.json` is self-contained and HMAC-signed; `verify-export` checks it offline.

## 4. Capture your real traffic

**Option A — SDK wrapper (one-line change):**

```python
from switchyard import Anthropic          # was: from anthropic import Anthropic

client = Anthropic()
client.messages.create(model="claude-fable-5", max_tokens=1024,
                       messages=[{"role": "user", "content": "…"}])
```

Tag work for later reporting:

```python
import switchyard
with switchyard.context(engagement="acme-q2", task_type="triage"):
    client.messages.create(...)   # tags attach to every event in the block
```

> Tags are stored only in `metadata`/`full` privacy modes. Enable with
> `switchyard config set privacy metadata`.

**Option B — proxy (no code change):**

```bash
switchyard proxy --port 4140
export ANTHROPIC_BASE_URL=http://127.0.0.1:4140
# now run Claude Code, a LangChain app, anything — it's audited
```

## 5. Watch live and gate CI

```bash
switchyard audit                          # live view of fallbacks as they land
switchyard check --max-rate 2% --since 24h   # non-zero exit if breached
```

## 6. Reframe a tripping request

```bash
switchyard rescope "exploit this binary"  # suggests a compliant reframe (nothing sent)
```

That's the whole loop: capture → detect → report → verify → re-scope. Everything stays on
your machine.
