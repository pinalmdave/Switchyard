"""Hash-chained local SQLite ledger (F-LGR-01).

Every request observed by Switchyard is appended to the ``requests`` table as a
canonical-JSON payload chained by ``entry_hash = sha256(prev_hash || payload || sequence)``.
:meth:`Ledger.verify` re-walks the chain and reports the first broken link.

Privacy modes (stored in ``meta``, default ``hash``):

- ``hash`` — stores ``prompt_sha256`` plus counts, timings, and model names only.
  Prompt bodies and engagement/task tags are never written.
- ``metadata`` — additionally stores ``engagement`` and ``task_type`` tags.
- ``full`` — additionally stores the prompt body. Explicit opt-in only.

Honesty note: a verified chain proves no recorded entry was altered, inserted, or
removed from the middle. It cannot prove entries were not truncated from the *tail*
after the fact — the signed export (F-LGR-02) adds the chain head needed for that.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any

GENESIS_HASH = "0" * 64
SCHEMA_VERSION = 1
DETECTION_METHODS = ("declared", "heuristic")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    sequence   INTEGER PRIMARY KEY,
    payload    TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    -- Generated columns: queryable, but cannot drift from the hashed payload.
    timestamp       TEXT GENERATED ALWAYS AS (json_extract(payload, '$.timestamp')) VIRTUAL,
    requested_model TEXT GENERATED ALWAYS AS (json_extract(payload, '$.requested_model')) VIRTUAL,
    served_model    TEXT GENERATED ALWAYS AS (json_extract(payload, '$.served_model')) VIRTUAL,
    engagement      TEXT GENERATED ALWAYS AS (json_extract(payload, '$.engagement')) VIRTUAL,
    task_type       TEXT GENERATED ALWAYS AS (json_extract(payload, '$.task_type')) VIRTUAL
);
CREATE TABLE IF NOT EXISTS fallback_events (
    id               INTEGER PRIMARY KEY,
    request_sequence INTEGER NOT NULL REFERENCES requests(sequence),
    detection_method TEXT NOT NULL,
    confidence       REAL NOT NULL,
    requested_model  TEXT NOT NULL,
    served_model     TEXT NOT NULL,
    details          TEXT,
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rescopes (
    id               INTEGER PRIMARY KEY,
    request_sequence INTEGER REFERENCES requests(sequence),
    template_name    TEXT,
    original_sha256  TEXT,
    suggestion       TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
"""


class PrivacyMode(StrEnum):
    """What the ledger is allowed to store about each request."""

    HASH = "hash"
    METADATA = "metadata"
    FULL = "full"


def switchyard_home() -> Path:
    """Return the Switchyard state directory (``SWITCHYARD_HOME`` or ``~/.switchyard``)."""
    override = os.environ.get("SWITCHYARD_HOME")
    return Path(override) if override else Path.home() / ".switchyard"


def default_ledger_path() -> Path:
    """Return the default ledger location inside :func:`switchyard_home`."""
    return switchyard_home() / "ledger.db"


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize ``payload`` deterministically (sorted keys, no whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(prev_hash: str, payload_json: str, sequence: int) -> str:
    """Return ``sha256(prev_hash || payload_json || sequence)`` as lowercase hex."""
    material = prev_hash.encode() + payload_json.encode() + str(sequence).encode()
    return hashlib.sha256(material).hexdigest()


def hash_prompt(prompt: str) -> str:
    """Return the SHA-256 hex digest of a prompt string."""
    return hashlib.sha256(prompt.encode()).hexdigest()


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class RequestRecord:
    """One observed model request, as reported by a capture surface.

    ``prompt`` is hashed before storage unless the privacy mode is ``full``;
    callers may pass a pre-computed ``prompt_sha256`` instead.
    """

    requested_model: str
    served_model: str | None = None
    prompt: str | None = None
    prompt_sha256: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    engagement: str | None = None
    task_type: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class LedgerEntry:
    """A request row as stored: payload plus its position in the hash chain."""

    sequence: int
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of re-walking the hash chain."""

    ok: bool
    entries_checked: int
    first_broken_sequence: int | None = None
    error: str | None = None


class Ledger:
    """Append-only, hash-chained request ledger backed by a local SQLite file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Capture surfaces record from worker threads (e.g. Starlette background
        # tasks); serialize all access through one lock instead of one connection
        # per thread so the chain head can never race.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('privacy', ?)",
                (PrivacyMode.HASH.value,),
            )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- privacy mode ------------------------------------------------------

    @property
    def privacy_mode(self) -> PrivacyMode:
        """The active privacy mode (what gets stored per request)."""
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = 'privacy'").fetchone()
        return PrivacyMode(row["value"])

    def set_privacy_mode(self, mode: PrivacyMode | str) -> PrivacyMode:
        """Persist a new privacy mode; applies to entries appended afterwards."""
        mode = PrivacyMode(mode)
        with self._lock, self._conn:
            self._conn.execute("UPDATE meta SET value = ? WHERE key = 'privacy'", (mode.value,))
        return mode

    # -- append ------------------------------------------------------------

    def _build_payload(self, record: RequestRecord, mode: PrivacyMode) -> dict[str, Any]:
        prompt_sha256 = record.prompt_sha256
        if prompt_sha256 is None and record.prompt is not None:
            prompt_sha256 = hash_prompt(record.prompt)
        payload: dict[str, Any] = {
            "timestamp": record.timestamp or _utcnow(),
            "requested_model": record.requested_model,
            "served_model": record.served_model,
            "prompt_sha256": prompt_sha256,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "latency_ms": record.latency_ms,
            "privacy": mode.value,
        }
        if mode in (PrivacyMode.METADATA, PrivacyMode.FULL):
            payload["engagement"] = record.engagement
            payload["task_type"] = record.task_type
        if mode is PrivacyMode.FULL:
            payload["prompt"] = record.prompt
        return payload

    def append_request(self, record: RequestRecord) -> LedgerEntry:
        """Append one request to the chain and return the stored entry."""
        with self._lock, self._conn:
            # BEGIN IMMEDIATE: take the write lock before reading the chain head
            # so concurrent appenders cannot race on (sequence, prev_hash).
            self._conn.execute("BEGIN IMMEDIATE")
            head = self._conn.execute(
                "SELECT sequence, entry_hash FROM requests ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = (head["sequence"] + 1) if head else 1
            prev_hash = head["entry_hash"] if head else GENESIS_HASH
            payload = self._build_payload(record, self.privacy_mode)
            payload_json = canonical_json(payload)
            entry_hash = compute_entry_hash(prev_hash, payload_json, sequence)
            self._conn.execute(
                "INSERT INTO requests (sequence, payload, prev_hash, entry_hash)"
                " VALUES (?, ?, ?, ?)",
                (sequence, payload_json, prev_hash, entry_hash),
            )
        return LedgerEntry(sequence, payload, prev_hash, entry_hash)

    def record_fallback(
        self,
        request_sequence: int,
        detection_method: str,
        confidence: float,
        requested_model: str,
        served_model: str,
        details: Mapping[str, Any] | None = None,
    ) -> int:
        """Record a detected fallback event; returns the event row id.

        Every event must carry a ``detection_method`` (declared | heuristic) and a
        ``confidence`` in [0, 1] — detection is probabilistic and is never presented
        as more certain than it is.
        """
        if detection_method not in DETECTION_METHODS:
            raise ValueError(
                f"detection_method must be one of {DETECTION_METHODS}, got {detection_method!r}"
            )
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {confidence}")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO fallback_events (request_sequence, detection_method, confidence,"
                " requested_model, served_model, details, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    request_sequence,
                    detection_method,
                    confidence,
                    requested_model,
                    served_model,
                    canonical_json(details) if details is not None else None,
                    _utcnow(),
                ),
            )
        return int(cursor.lastrowid or 0)

    def record_rescope(
        self,
        suggestion: str,
        request_sequence: int | None = None,
        template_name: str | None = None,
        original_sha256: str | None = None,
    ) -> int:
        """Record a re-scope suggestion; returns the row id."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO rescopes (request_sequence, template_name, original_sha256,"
                " suggestion, created_at) VALUES (?, ?, ?, ?, ?)",
                (request_sequence, template_name, original_sha256, suggestion, _utcnow()),
            )
        return int(cursor.lastrowid or 0)

    # -- read --------------------------------------------------------------

    def entries(self) -> Iterator[LedgerEntry]:
        """Yield all chain entries in sequence order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sequence, payload, prev_hash, entry_hash FROM requests ORDER BY sequence"
            ).fetchall()
        for row in rows:
            yield LedgerEntry(
                sequence=row["sequence"],
                payload=json.loads(row["payload"]),
                prev_hash=row["prev_hash"],
                entry_hash=row["entry_hash"],
            )

    def request_count(self) -> int:
        """Number of requests recorded."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM requests").fetchone()
        return int(row["n"])

    def fallback_count(self) -> int:
        """Number of fallback events recorded."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM fallback_events").fetchone()
        return int(row["n"])

    # -- verify ------------------------------------------------------------

    def verify(self) -> VerifyResult:
        """Re-walk the chain; report the first broken link, if any.

        Checks that sequences are contiguous from 1, that each ``prev_hash`` equals
        the previous ``entry_hash`` (genesis for the first), and that each
        ``entry_hash`` recomputes from its stored payload.
        """
        prev_hash = GENESIS_HASH
        expected_sequence = 1
        checked = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT sequence, payload, prev_hash, entry_hash FROM requests ORDER BY sequence"
            ).fetchall()
        for row in rows:
            sequence: int = row["sequence"]
            if sequence != expected_sequence:
                return VerifyResult(
                    ok=False,
                    entries_checked=checked,
                    first_broken_sequence=sequence,
                    error=f"sequence gap: expected {expected_sequence}, found {sequence}",
                )
            if row["prev_hash"] != prev_hash:
                return VerifyResult(
                    ok=False,
                    entries_checked=checked,
                    first_broken_sequence=sequence,
                    error=f"broken link: prev_hash of entry {sequence} does not match chain",
                )
            recomputed = compute_entry_hash(prev_hash, row["payload"], sequence)
            if recomputed != row["entry_hash"]:
                return VerifyResult(
                    ok=False,
                    entries_checked=checked,
                    first_broken_sequence=sequence,
                    error=f"hash mismatch: entry {sequence} does not match its payload",
                )
            prev_hash = row["entry_hash"]
            expected_sequence += 1
            checked += 1
        return VerifyResult(ok=True, entries_checked=checked)
