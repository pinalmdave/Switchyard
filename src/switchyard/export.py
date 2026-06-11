"""Signed, self-contained ledger export + offline verification (F-LGR-02).

``switchyard export --engagement acme-q2 --format json`` produces a portable
document containing the selected entries, the chain head they hash up to, and an
HMAC-SHA256 signature over a canonical serialization. ``switchyard verify-export
<file>`` re-checks both the internal hash chain and the signature **offline**,
with no access to the original ledger.

The signing key is generated once into ``~/.switchyard/signing.key`` (0600 where
the OS allows). The exact byte layout that gets signed is specified in
``docs/LEDGER_FORMAT.md`` so third parties can write independent verifiers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from switchyard.ledger import (
    GENESIS_HASH,
    Ledger,
    canonical_json,
    compute_entry_hash,
    switchyard_home,
)

EXPORT_VERSION = 1
SIGNATURE_ALGORITHM = "HMAC-SHA256"
_KEY_BYTES = 32


def signing_key_path() -> Path:
    """Location of the HMAC signing key."""
    return switchyard_home() / "signing.key"


def ensure_signing_key(path: Path | None = None) -> bytes:
    """Return the signing key, generating it once if absent.

    The key is stored hex-encoded. On POSIX the file is created with 0600
    permissions; on Windows the flag is a best-effort no-op.
    """
    key_path = path if path is not None else signing_key_path()
    if key_path.exists():
        return bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(_KEY_BYTES)
    # Create restrictively where supported, then write.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key.hex())
    return key


@dataclass(frozen=True)
class ExportDocument:
    """An export in memory: payload dict + the bytes that were/are signed."""

    document: dict[str, Any]

    @property
    def signature(self) -> str:
        return str(self.document["signature"]["value"])

    def to_json(self) -> str:
        return json.dumps(self.document, indent=2, sort_keys=True)


def signing_payload(
    entries: list[dict[str, Any]], chain_head: str, engagement: str | None
) -> dict[str, Any]:
    """The signed core of an export (everything except the signature block).

    Kept separate and canonicalized so independent verifiers can reproduce
    exactly what gets signed (see docs/LEDGER_FORMAT.md).
    """
    return {
        "export_version": EXPORT_VERSION,
        "engagement": engagement,
        "entry_count": len(entries),
        "chain_head": chain_head,
        "entries": entries,
    }


def _sign(key: bytes, payload: dict[str, Any]) -> str:
    message = canonical_json(payload).encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def build_export(
    ledger: Ledger, engagement: str | None = None, key: bytes | None = None
) -> ExportDocument:
    """Collect entries (optionally filtered by engagement) and sign them."""
    signing_key = key if key is not None else ensure_signing_key()
    entries: list[dict[str, Any]] = []
    chain_head = GENESIS_HASH
    for entry in ledger.entries():
        chain_head = entry.entry_hash
        if engagement is not None and entry.payload.get("engagement") != engagement:
            continue
        entries.append(
            {
                "sequence": entry.sequence,
                "payload": entry.payload,
                "prev_hash": entry.prev_hash,
                "entry_hash": entry.entry_hash,
            }
        )
    payload = signing_payload(entries, chain_head, engagement)
    signature = _sign(signing_key, payload)
    document = dict(payload)
    document["signature"] = {"algorithm": SIGNATURE_ALGORITHM, "value": signature}
    return ExportDocument(document=document)


def export_markdown(doc: ExportDocument) -> str:
    """Human-readable rendering of an export (the JSON is the source of truth)."""
    d = doc.document
    lines = [
        "# Switchyard signed export",
        "",
        f"- Export version: {d['export_version']}",
        f"- Engagement: {d['engagement'] or '(all)'}",
        f"- Entries: {d['entry_count']}",
        f"- Chain head: `{d['chain_head']}`",
        f"- Signature ({d['signature']['algorithm']}): `{d['signature']['value']}`",
        "",
        "| Seq | Timestamp | Requested | Served | Prompt SHA-256 |",
        "|---:|---|---|---|---|",
    ]
    for entry in d["entries"]:
        payload = entry["payload"]
        sha = (payload.get("prompt_sha256") or "")[:16]
        lines.append(
            f"| {entry['sequence']} | {payload.get('timestamp', '')} | "
            f"{payload.get('requested_model', '')} | {payload.get('served_model') or ''} | "
            f"{sha} |"
        )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ExportVerifyResult:
    """Outcome of verifying an export file offline."""

    ok: bool
    signature_valid: bool
    chain_valid: bool
    entry_count: int
    error: str | None = None


def verify_export_document(document: dict[str, Any], key: bytes) -> ExportVerifyResult:
    """Verify an export's signature and internal hash chain (offline).

    Signature is checked first (constant-time). Then each entry's ``entry_hash``
    is recomputed from its payload and linked to the previous entry; filtered
    exports may skip sequence numbers, so links are validated only between
    entries that are actually adjacent in the original chain.
    """
    sig_block = document.get("signature")
    if not isinstance(sig_block, dict) or "value" not in sig_block:
        return ExportVerifyResult(False, False, False, 0, "missing signature block")

    entries = document.get("entries", [])
    payload = signing_payload(entries, document.get("chain_head", ""), document.get("engagement"))
    # Preserve declared version even if it differs from the current constant.
    payload["export_version"] = document.get("export_version")
    expected = _sign(key, payload)
    signature_valid = hmac.compare_digest(expected, str(sig_block["value"]))
    if not signature_valid:
        return ExportVerifyResult(
            False, False, False, len(entries), "signature mismatch (wrong key or tampered export)"
        )

    prev_seq: int | None = None
    prev_hash_for_adjacent = GENESIS_HASH
    for entry in entries:
        recomputed = compute_entry_hash(
            entry["prev_hash"], canonical_json(entry["payload"]), entry["sequence"]
        )
        if recomputed != entry["entry_hash"]:
            return ExportVerifyResult(
                False,
                True,
                False,
                len(entries),
                f"hash mismatch at sequence {entry['sequence']}",
            )
        if entry["sequence"] == 1 and entry["prev_hash"] != GENESIS_HASH:
            return ExportVerifyResult(
                False, True, False, len(entries), "first entry does not chain to genesis"
            )
        if (
            prev_seq is not None
            and entry["sequence"] == prev_seq + 1
            and entry["prev_hash"] != prev_hash_for_adjacent
        ):
            return ExportVerifyResult(
                False,
                True,
                False,
                len(entries),
                f"broken link between {prev_seq} and {entry['sequence']}",
            )
        prev_seq = entry["sequence"]
        prev_hash_for_adjacent = entry["entry_hash"]

    return ExportVerifyResult(True, True, True, len(entries))


def verify_export_file(path: str | Path, key: bytes | None = None) -> ExportVerifyResult:
    """Load an export JSON file and verify it offline."""
    signing_key = key if key is not None else ensure_signing_key()
    raw = Path(path).read_text(encoding="utf-8")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ExportVerifyResult(False, False, False, 0, f"invalid JSON: {exc}")
    return verify_export_document(document, signing_key)
