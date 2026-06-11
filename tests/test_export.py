"""Signed export tests (F-LGR-02): round-trip, tamper detection, spec vector."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from helpers import FABLE, OPUS
from switchyard.cli import main
from switchyard.export import (
    GENESIS_HASH,
    build_export,
    ensure_signing_key,
    export_markdown,
    signing_key_path,
    verify_export_document,
    verify_export_file,
)
from switchyard.ledger import Ledger, RequestRecord, canonical_json, compute_entry_hash


def seed(engagement_split: bool = False) -> None:
    with Ledger() as ledger:
        if engagement_split:
            ledger.set_privacy_mode("metadata")
        for i in range(4):
            eng = ("acme" if i % 2 == 0 else "beta") if engagement_split else None
            entry = ledger.append_request(
                RequestRecord(
                    requested_model=FABLE,
                    served_model=OPUS if i == 1 else FABLE,
                    prompt=f"req {i}",
                    engagement=eng,
                )
            )
            if i == 1:
                ledger.record_fallback(entry.sequence, "declared", 1.0, FABLE, OPUS)


# -- spec vector ------------------------------------------------------------------


def test_documented_entry_hash_vector() -> None:
    """The worked example in docs/LEDGER_FORMAT.md must match the code."""
    payload = canonical_json(
        {"requested_model": "claude-fable-5", "served_model": "claude-opus-4-8"}
    )
    assert payload == '{"requested_model":"claude-fable-5","served_model":"claude-opus-4-8"}'
    digest = compute_entry_hash(GENESIS_HASH, payload, 1)
    assert digest == "a15a2358ad15506de493687553e4a9d37d4a503040c0670c0b321afb604a2bbe"


# -- signing key ------------------------------------------------------------------


def test_signing_key_created_once(isolated_home: Path) -> None:
    key1 = ensure_signing_key()
    assert signing_key_path().exists()
    assert len(key1) == 32
    key2 = ensure_signing_key()
    assert key1 == key2  # stable across calls


# -- round trip -------------------------------------------------------------------


def test_export_verify_round_trip() -> None:
    seed()
    with Ledger() as ledger:
        doc = build_export(ledger)
    assert doc.document["entry_count"] == 4
    assert doc.document["chain_head"] != GENESIS_HASH
    result = verify_export_document(doc.document, ensure_signing_key())
    assert result.ok
    assert result.signature_valid
    assert result.chain_valid
    assert result.entry_count == 4


def test_filtered_export_keeps_full_chain_head() -> None:
    seed(engagement_split=True)
    with Ledger() as ledger:
        full_head = list(ledger.entries())[-1].entry_hash
        doc = build_export(ledger, engagement="acme")
    assert doc.document["entry_count"] == 2  # only acme entries
    assert doc.document["chain_head"] == full_head  # but commits to the whole chain
    assert verify_export_document(doc.document, ensure_signing_key()).ok


# -- tamper detection -------------------------------------------------------------


def test_tampered_payload_fails_verification() -> None:
    seed()
    with Ledger() as ledger:
        doc = build_export(ledger)
    key = ensure_signing_key()
    doc.document["entries"][0]["payload"]["requested_model"] = "claude-opus-4-8"
    result = verify_export_document(doc.document, key)
    assert not result.ok
    assert not result.signature_valid  # signature covers the payload


def test_tampered_entry_hash_after_resign_fails_chain() -> None:
    seed()
    with Ledger() as ledger:
        doc = build_export(ledger)
    # Tamper an entry_hash AND re-sign with the right key: signature passes,
    # but the recomputed chain hash no longer matches.
    from switchyard.export import _sign, signing_payload

    key = ensure_signing_key()
    doc.document["entries"][2]["entry_hash"] = "f" * 64
    payload = signing_payload(
        doc.document["entries"], doc.document["chain_head"], doc.document["engagement"]
    )
    doc.document["signature"]["value"] = _sign(key, payload)
    result = verify_export_document(doc.document, key)
    assert result.signature_valid
    assert not result.chain_valid
    assert "hash mismatch" in (result.error or "")


def test_wrong_key_fails_verification() -> None:
    seed()
    with Ledger() as ledger:
        doc = build_export(ledger, key=b"\x01" * 32)
    result = verify_export_document(doc.document, key=b"\x02" * 32)
    assert not result.ok
    assert "signature mismatch" in (result.error or "")


def test_missing_signature_block() -> None:
    result = verify_export_document({"entries": []}, key=b"\x00" * 32)
    assert not result.ok
    assert "missing signature" in (result.error or "")


# -- file + CLI -------------------------------------------------------------------


def test_verify_export_file_round_trip(tmp_path: Path) -> None:
    seed()
    with Ledger() as ledger:
        doc = build_export(ledger)
    path = tmp_path / "export.json"
    path.write_text(doc.to_json(), encoding="utf-8")
    assert verify_export_file(path).ok


def test_verify_export_file_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    result = verify_export_file(path)
    assert not result.ok
    assert "invalid JSON" in (result.error or "")


def test_markdown_export_renders() -> None:
    seed()
    with Ledger() as ledger:
        doc = build_export(ledger)
    md = export_markdown(doc)
    assert "# Switchyard signed export" in md
    assert doc.signature in md


def test_cli_export_and_verify_round_trip(runner: CliRunner, tmp_path: Path) -> None:
    seed()
    out = tmp_path / "acme.json"
    result = runner.invoke(main, ["export", "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    verify = runner.invoke(main, ["verify-export", str(out)])
    assert verify.exit_code == 0
    assert "export verified" in verify.output


def test_cli_verify_export_detects_tampering(runner: CliRunner, tmp_path: Path) -> None:
    seed()
    out = tmp_path / "acme.json"
    runner.invoke(main, ["export", "--output", str(out)])
    document = json.loads(out.read_text(encoding="utf-8"))
    document["entries"][0]["payload"]["served_model"] = "claude-opus-4-8"
    out.write_text(json.dumps(document), encoding="utf-8")
    result = runner.invoke(main, ["verify-export", str(out)])
    assert result.exit_code == 1
    assert "export invalid" in result.output
    json_result = runner.invoke(main, ["verify-export", str(out), "--json"])
    assert json.loads(json_result.output)["ok"] is False


def test_cli_export_stdout_json(runner: CliRunner) -> None:
    seed()
    result = runner.invoke(main, ["export"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["export_version"] == 1
    assert "signature" in payload


def test_cli_export_markdown(runner: CliRunner) -> None:
    seed()
    result = runner.invoke(main, ["export", "--format", "md"])
    assert result.exit_code == 0
    assert "Switchyard signed export" in result.output
