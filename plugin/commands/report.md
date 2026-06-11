---
description: Show and explain the Switchyard fallback-rate report.
argument-hint: "[--since 7d] [--by task_type|engagement|model]"
allowed-tools: Bash(switchyard:*)
---

Generate and explain a Switchyard fallback report.

1. Run `switchyard report --format json $ARGUMENTS` and parse the JSON.
2. Present the headline numbers: total requests, fallback rate, by-method split
   (with average confidence), the top tripping groups, and the estimated retry
   token cost.
3. Be honest about detection: declared fallbacks are certain; heuristic ones are
   probabilistic — never present a heuristic event as a fact.
4. If the rate looks high for the user's risk tolerance, suggest `/switchyard:rescope`
   for the worst-offending task type, and mention `switchyard check --max-rate` for CI.

If `switchyard` is not installed, tell the user to run `pip install switchyard-ai`.
