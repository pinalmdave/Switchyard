# Switchyard ledger & export format

This document specifies the on-disk hash chain and the signed export format
precisely enough that a third party can write an **independent verifier** without
reading the Switchyard source. The test suite checks the worked example below
against the real implementation, so this spec cannot silently drift.

## 1. The hash chain (ledger)

The ledger is a local SQLite database (`~/.switchyard/ledger.db`). Each row in
the `requests` table is one observed request, appended in order:

| column       | meaning                                              |
| ------------ | ---------------------------------------------------- |
| `sequence`   | 1-based, contiguous, strictly increasing             |
| `payload`    | canonical JSON of the recorded fields (see §2)       |
| `prev_hash`  | `entry_hash` of the previous row (genesis for seq 1) |
| `entry_hash` | the chain hash defined in §3                          |

The genesis hash (the `prev_hash` of sequence 1) is 64 zeros:

```
0000000000000000000000000000000000000000000000000000000000000000
```

## 2. Canonical JSON

Every hash and signature is computed over **canonical JSON**, defined as
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`:

- object keys sorted lexicographically,
- no insignificant whitespace (`,` and `:` separators),
- non-ASCII characters emitted literally (UTF-8), not `\u`-escaped.

The `payload` stored in each row is already canonical JSON. Its fields depend on
the privacy mode; in the default `hash` mode the keys are:

```
input_tokens, latency_ms, output_tokens, privacy,
prompt_sha256, requested_model, served_model, timestamp
```

`metadata` mode adds `engagement` and `task_type`; `full` mode additionally adds
`prompt`. Prompt bodies are never present in `hash` or `metadata` mode.

## 3. Entry hash

For each row:

```
entry_hash = SHA256( prev_hash_ascii || payload_json_utf8 || sequence_ascii )
```

where `||` is byte concatenation, `prev_hash_ascii` is the 64-character
lowercase-hex previous hash, `payload_json_utf8` is the canonical JSON payload
encoded as UTF-8, and `sequence_ascii` is the base-10 sequence number as ASCII.

Worked example (one entry chaining to genesis):

- `prev_hash` = `0000...0000` (64 zeros)
- `payload` (canonical JSON):
  ```
  {"requested_model":"claude-fable-5","served_model":"claude-opus-4-8"}
  ```
- `sequence` = `1`

Then:

```
entry_hash = SHA256(b"0000...0000" + payload_utf8 + b"1")
           = a15a2358ad15506de493687553e4a9d37d4a503040c0670c0b321afb604a2bbe
```

This exact digest is asserted against the implementation in
`tests/test_export.py::test_documented_entry_hash_vector`, so the spec cannot
drift from the code.

## 4. Verifying the chain

A verifier walks rows in `sequence` order and checks:

1. `sequence` starts at 1 and increases by exactly 1 (no gaps);
2. `prev_hash` of row 1 is the genesis hash; for every later row it equals the
   previous row's `entry_hash`;
3. recomputing §3 from the stored `payload` reproduces the stored `entry_hash`.

The first failing row is the tamper point. Note: a verifier with **only** the
database cannot detect entries removed from the *tail* of the chain — for that
you need an external record of the chain head, which the signed export provides.

## 5. Signed export

`switchyard export` emits a JSON document. The signature covers the **signing
payload** — the document with the `signature` block removed:

```json
{
  "export_version": 1,
  "engagement": "acme-q2",
  "entry_count": 2,
  "chain_head": "<entry_hash of the last entry in the full ledger>",
  "entries": [
    {"sequence": 1, "payload": { ... }, "prev_hash": "...", "entry_hash": "..."},
    {"sequence": 2, "payload": { ... }, "prev_hash": "...", "entry_hash": "..."}
  ]
}
```

`chain_head` is the `entry_hash` of the last entry in the **full** ledger at
export time (not the last *included* entry) — so a filtered export still commits
to the complete chain length, closing the tail-truncation gap from §4.

The signature is:

```
signature.value = HMAC_SHA256( signing_key, canonical_json(signing_payload) )
signature.algorithm = "HMAC-SHA256"
```

## 6. Verifying an export (offline)

1. Remove the `signature` block; reconstruct the signing payload from
   `export_version`, `engagement`, `entry_count`, `chain_head`, `entries`.
2. Recompute the HMAC with the signing key and compare in constant time.
3. For each entry, recompute §3 and confirm it matches `entry_hash`.
4. For entries that are adjacent in the original chain (`sequence` differs by 1),
   confirm `prev_hash` links them; confirm sequence 1 (if present) chains to
   genesis.

The signing key lives at `~/.switchyard/signing.key` (hex-encoded, 32 bytes,
created with `0600` permissions where the OS supports it). The HMAC construction
means **verification requires the same key that produced the export** — this is a
tamper-evidence and provenance mechanism for the holder of the key, not a public
signature. A future format version may add asymmetric signatures; verifiers
should branch on `export_version` / `signature.algorithm`.
