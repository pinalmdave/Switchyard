---
description: Suggest a compliant re-scope that keeps a request on the frontier model.
argument-hint: "<prompt to reframe>"
allowed-tools: Bash(switchyard:*)
---

The user wants to reframe a request that is (or might be) tripping the safeguard
classifiers and getting silently served by Opus 4.8.

1. Run `switchyard rescope "$ARGUMENTS" --json` and parse the result.
2. Show the matched template(s), the rationale, and the suggested reframe.
3. Explain *why* the reframe helps: it makes the legitimate defensive intent,
   authorization, and use-case explicit without changing the actual technical need.
4. Make clear this is a suggestion only — nothing was sent anywhere. The user can
   copy the reframe and try it. If they want a tailored rewrite using their own
   Claude key, they can run `switchyard rescope "..." --llm`.

If no template matched, suggest the `--llm` flag or refining the request with the
defensive purpose stated up front.

If `switchyard` is not installed, tell the user to run `pip install switchyard-ai`.
