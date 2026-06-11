---
description: Confirm Switchyard capture is working and summarize fallback events so far.
allowed-tools: Bash(switchyard:*)
---

Run Switchyard's audit and interpret it for the user.

1. Run `switchyard audit --once` to confirm capture is working and get current status.
2. Run `switchyard report --format json` and parse it.
3. Summarize in plain language:
   - Is capture working? (If "no traffic captured yet", tell the user to wrap their
     client with `from switchyard import Anthropic` or start `switchyard proxy`.)
   - The current fallback rate and how many events, split by detection method.
     Always state the confidence — declared events are certain (1.0); heuristic
     events are probabilistic (0.5–0.8) and must not be presented as facts.
   - Which task types or engagements trip most often.
4. If the fallback rate is non-trivial, offer to run `/switchyard:rescope` on a
   specific prompt to suggest a compliant reframe.

If `switchyard` is not installed, tell the user to run `pip install switchyard-ai`.
