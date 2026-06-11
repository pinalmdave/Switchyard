"""Ledger tests (F-LGR-01): hash chain integrity and privacy-mode guarantees."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from switchyard.ledger import (
    GENESIS_HASH,
    Ledger,
    PrivacyMode,
    RequestRecord,
    canonical_json,
    default_ledger_path,
    hash_prompt,
    switchyard_home,
)

FABLE = "claude-fable-5"
OPUS = "claude-opus-4-8"


def make_record(i: int = 1, prompt: str | None = None) -> RequestRecord:
    return RequestRecord(
        requested_model=FABLE,
        served_model=FABLE,
        prompt=prompt or f"analyze sample {i}",
        input_tokens=100 + i,
        output_tokens=200 + i,
        latency_ms=1234.5,
        engagement="acme-q2",
        task_type="malware-triage",
    )


# -- paths -------------------------------------------------------------------


def test_home_respects_env(isolated_home: Path) -> None:
    assert switchyard_home() == isolated_home
    assert default_ledger_path() == isolated_home / "ledger.db"


def test_home_defaults_to_user_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWITCHYARD_HOME")
    assert switchyard_home() == Path.home() / ".switchyard"


# -- chain basics ------------------------------------------------------------


def test_empty_ledger_verifies() -> None:
    with Ledger() as ledger:
        result = ledger.verify()
    assert result.ok
    assert result.entries_checked == 0


def test_chain_links_and_verifies() -> None:
    with Ledger() as ledger:
        first = ledger.append_request(make_record(1))
        second = ledger.append_request(make_record(2))
        assert first.sequence == 1
        assert first.prev_hash == GENESIS_HASH
        assert second.sequence == 2
        assert second.prev_hash == first.entry_hash
        result = ledger.verify()
    assert result.ok
    assert result.entries_checked == 2


def test_entries_round_trip() -> None:
    with Ledger() as ledger:
        stored = ledger.append_request(make_record(1))
        (loaded,) = list(ledger.entries())
    assert loaded == stored
    assert loaded.payload["requested_model"] == FABLE


def test_reopen_continues_chain() -> None:
    with Ledger() as ledger:
        ledger.append_request(make_record(1))
        head = ledger.append_request(make_record(2))
    with Ledger() as ledger:
        third = ledger.append_request(make_record(3))
        assert third.sequence == 3
        assert third.prev_hash == head.entry_hash
        assert ledger.verify().ok


# -- tamper detection (property: any mutation breaks verify) ------------------


def _tamper(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(sql, params)
    conn.close()


@settings(max_examples=25, deadline=None)
@given(
    prompts=st.lists(st.text(min_size=1, max_size=80), min_size=1, max_size=8),
    mutation=st.sampled_from(["payload", "prev_hash", "entry_hash", "delete_middle"]),
    data=st.data(),
)
def test_any_mutation_breaks_verify(prompts: list[str], mutation: str, data: st.DataObject) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ledger.db"
        with Ledger(db_path) as ledger:
            for i, prompt in enumerate(prompts):
                ledger.append_request(make_record(i, prompt=prompt))
            assert ledger.verify().ok

        n = len(prompts)
        if mutation == "delete_middle":
            # Deleting the tail entry is undetectable without an external chain
            # head (that arrives with signed export, F-LGR-02) — so delete a
            # non-tail entry, which requires at least two.
            if n < 2:
                return
            target = data.draw(st.integers(min_value=1, max_value=n - 1))
            _tamper(db_path, "DELETE FROM requests WHERE sequence = ?", (target,))
        else:
            target = data.draw(st.integers(min_value=1, max_value=n))
            if mutation == "payload":
                tampered = canonical_json({"requested_model": OPUS, "forged": True})
                _tamper(
                    db_path,
                    "UPDATE requests SET payload = ? WHERE sequence = ?",
                    (tampered, target),
                )
            elif mutation == "prev_hash":
                _tamper(
                    db_path,
                    "UPDATE requests SET prev_hash = ? WHERE sequence = ?",
                    ("f" * 64, target),
                )
            else:
                _tamper(
                    db_path,
                    "UPDATE requests SET entry_hash = ? WHERE sequence = ?",
                    ("f" * 64, target),
                )

        with Ledger(db_path) as ledger:
            result = ledger.verify()
        assert not result.ok
        assert result.first_broken_sequence is not None
        assert result.error


def test_verify_reports_first_broken_link() -> None:
    db_path = default_ledger_path()
    with Ledger() as ledger:
        for i in range(5):
            ledger.append_request(make_record(i))
    _tamper(db_path, "UPDATE requests SET prev_hash = ? WHERE sequence = 3", ("a" * 64,))
    with Ledger() as ledger:
        result = ledger.verify()
    assert not result.ok
    assert result.first_broken_sequence == 3
    assert result.entries_checked == 2


# -- privacy modes -------------------------------------------------------------


def read_db_bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_default_privacy_is_hash() -> None:
    with Ledger() as ledger:
        assert ledger.privacy_mode is PrivacyMode.HASH


def test_hash_mode_never_writes_prompt_bytes_or_tags() -> None:
    secret = "SECRET-EXPLOIT-PAYLOAD-9000"
    with Ledger() as ledger:
        entry = ledger.append_request(make_record(1, prompt=secret))
    assert entry.payload["prompt_sha256"] == hash_prompt(secret)
    assert "prompt" not in entry.payload
    assert "engagement" not in entry.payload
    assert "task_type" not in entry.payload
    raw = read_db_bytes(default_ledger_path())
    assert secret.encode() not in raw
    assert b"acme-q2" not in raw


def test_metadata_mode_adds_tags_but_not_body() -> None:
    secret = "SECRET-METADATA-MODE-PROMPT"
    with Ledger() as ledger:
        ledger.set_privacy_mode("metadata")
        entry = ledger.append_request(make_record(1, prompt=secret))
    assert entry.payload["engagement"] == "acme-q2"
    assert entry.payload["task_type"] == "malware-triage"
    assert "prompt" not in entry.payload
    assert secret.encode() not in read_db_bytes(default_ledger_path())


def test_full_mode_stores_body() -> None:
    with Ledger() as ledger:
        ledger.set_privacy_mode(PrivacyMode.FULL)
        entry = ledger.append_request(make_record(1, prompt="visible prompt"))
    assert entry.payload["prompt"] == "visible prompt"
    assert b"visible prompt" in read_db_bytes(default_ledger_path())


def test_precomputed_prompt_sha256_is_used() -> None:
    digest = hash_prompt("elsewhere")
    record = RequestRecord(requested_model=FABLE, prompt_sha256=digest)
    with Ledger() as ledger:
        entry = ledger.append_request(record)
    assert entry.payload["prompt_sha256"] == digest


def test_invalid_privacy_mode_rejected() -> None:
    with Ledger() as ledger, pytest.raises(ValueError):
        ledger.set_privacy_mode("everything")


# -- fallback events and rescopes ---------------------------------------------


def test_record_fallback_and_count() -> None:
    with Ledger() as ledger:
        entry = ledger.append_request(make_record(1))
        event_id = ledger.record_fallback(
            request_sequence=entry.sequence,
            detection_method="declared",
            confidence=1.0,
            requested_model=FABLE,
            served_model=OPUS,
            details={"source": "response.model"},
        )
        assert event_id == 1
        assert ledger.fallback_count() == 1
        assert ledger.request_count() == 1


def test_fallback_requires_known_method() -> None:
    with Ledger() as ledger:
        entry = ledger.append_request(make_record(1))
        with pytest.raises(ValueError, match="detection_method"):
            ledger.record_fallback(entry.sequence, "vibes", 0.9, FABLE, OPUS)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, 2.0])
def test_fallback_confidence_bounds(confidence: float) -> None:
    with Ledger() as ledger:
        entry = ledger.append_request(make_record(1))
        with pytest.raises(ValueError, match="confidence"):
            ledger.record_fallback(entry.sequence, "heuristic", confidence, FABLE, OPUS)


def test_record_rescope() -> None:
    with Ledger() as ledger:
        rescope_id = ledger.record_rescope(
            suggestion="Reframe as defensive impact analysis",
            template_name="exploit-to-defensive",
            original_sha256=hash_prompt("exploit this binary"),
        )
        assert rescope_id == 1
