# How Switchyard detects fallbacks — and what it cannot detect

Detection is **probabilistic**. Every fallback event Switchyard records carries a
`detection_method` and a `confidence`, and every surface (CLI, reports, plugin, MCP)
shows both. A heuristic is never presented as a fact. This document says plainly what
each method can and cannot catch, because honesty about limits is a feature.

## Summary

| Method      | What it catches                                                | What it misses                                                      | Confidence |
| ----------- | -------------------------------------------------------------- | ------------------------------------------------------------------- | ---------- |
| `declared`  | The response itself declares a different serving model          | Responses that declare nothing, or that declare incorrectly          | `1.0`      |
| `heuristic` | Undeclared responses whose timing profile deviates strongly     | Subtle fallbacks; anything before 30 baseline samples; cause unknown | `0.5–0.8`  |

## Declared-model detection (`declared`)

Switchyard compares the model you requested against what the API response declares:

1. the `model` field in the response body, and
2. a served-by response header (`anthropic-served-by` / `x-served-by`), when present.

If either declares a different model **family**, that is a fallback with
`confidence = 1.0` — the server said so itself.

**Snapshot aliases are not fallbacks.** `claude-fable-5-20260609` and
`claude-fable-5` are the same model; Switchyard normalizes date-suffixed snapshot
ids before comparing, so an alias can never be reported as a fallback.

**Limits, stated plainly:**

- If the response declares the requested model, Switchyard believes it. Declared
  detection **cannot catch a server that misreports** the serving model. We have no
  evidence this happens; we also cannot rule it out from the client side.
- If the response declares nothing (no `model` field, no header), declared detection
  returns **no verdict** — it does not guess. Such responses go to the heuristic
  detector, if enabled and ready.
- Header-based detection is speculative: those headers are not part of the documented
  API surface and may never appear in your traffic. Body-field detection is the one
  that does the work in practice.

## Heuristic corroboration (`heuristic`)

For responses that declare nothing, Switchyard keeps rolling per-model baselines
(local SQLite, `~/.switchyard/baselines.db`) of two timing signals:

- `latency_ms_per_output_token` — wall-clock latency divided by output tokens
- `output_tokens_per_sec` — output tokens divided by wall-clock seconds

Baselines learn **only from declared-confirmed responses** (the response said it was
served by the requested model), so unverified traffic can never poison them. They are
Welford running statistics (count / mean / variance) — no raw samples are retained.

A heuristic event fires only when **all** of the following hold:

1. Every signal has at least **30 baseline samples** (`min_samples`, configurable).
   Until then, heuristics are **off** — silently collecting baselines, reporting nothing.
2. **Both** signals deviate from their baseline by at least **3.0 standard deviations**
   (`z_threshold`, configurable).

### Confidence formula

```
confidence = clamp(0.5 + 0.05 × (z_min − z_threshold), 0.5, 0.8)
```

where `z_min` is the *smaller* of the two deviating |z|-scores. At the threshold
(z = 3.0) confidence is 0.5; it saturates at 0.8 by z = 9.0. The ceiling of 0.8 is
deliberate: timing evidence alone never justifies more.

**Limits, stated plainly:**

- The two signals are mathematically related (one is approximately the reciprocal of
  the other). Requiring both to deviate is a corroboration check against arithmetic
  edge cases — it is **not** two independent witnesses.
- Timing deviations have many causes that are not fallbacks: network congestion, API
  load, long-context requests, streaming vs. non-streaming, your own machine. A
  heuristic event means "this response did not look like your baseline," nothing more.
  Expect false positives at roughly the tail probability of your traffic's timing
  distribution; raise `z_threshold` to trade recall for precision.
- A heuristic event cannot say **which** model served the request — `served_model`
  is empty on heuristic events, by design.
- A fallback model with a similar speed profile will not be caught at all.

## What Switchyard does not attempt

- **Behavioral fingerprinting** (classifying outputs by writing style, capability
  probes, logprob signatures) is out of scope for the OSS tool — it needs corpus
  scale that a local tool does not have. The `Detector` protocol in
  `src/switchyard/detect.py` is the stable plug-in point if you want to build one.
- **Content-quality judgments.** Switchyard never reads or scores your prompts or
  completions; in the default privacy mode it only ever sees hashes and timings.
