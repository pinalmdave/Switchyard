"""SDK wrapper tests (F-CLI-01): signature parity, fixture replay, failure isolation.

Fixture responses are replayed through the *real* ``anthropic`` SDK using an
``httpx.MockTransport`` — no live network, no mocking of Switchyard internals.
"""

from __future__ import annotations

import inspect
from typing import Any

import anthropic
import httpx
import pytest

import switchyard
import switchyard.client
from helpers import FABLE, OPUS, message_body, sse_body
from switchyard.ledger import Ledger


@pytest.fixture(autouse=True)
def reset_warn_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(switchyard.client, "_warned_once", False)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def json_handler(body: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def sse_handler(model: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=sse_body(model), headers={"content-type": "text/event-stream"}
        )

    return httpx.MockTransport(handler)


def make_client(transport: httpx.MockTransport) -> switchyard.Anthropic:
    return switchyard.Anthropic(api_key="test-key", http_client=httpx.Client(transport=transport))


def make_async_client(transport: httpx.MockTransport) -> switchyard.AsyncAnthropic:
    return switchyard.AsyncAnthropic(
        api_key="test-key", http_client=httpx.AsyncClient(transport=transport)
    )


def create_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": FABLE,
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "triage this CVE"}],
    }
    kwargs.update(overrides)
    return kwargs


# -- signature parity -----------------------------------------------------------


def test_create_signature_matches_sdk() -> None:
    ours = make_client(json_handler(message_body()))
    theirs = anthropic.Anthropic(api_key="test-key")
    assert inspect.signature(ours.messages.create) == inspect.signature(theirs.messages.create)


def test_stream_signature_matches_sdk() -> None:
    ours = make_client(json_handler(message_body()))
    theirs = anthropic.Anthropic(api_key="test-key")
    assert inspect.signature(ours.messages.stream) == inspect.signature(theirs.messages.stream)


def test_async_create_signature_matches_sdk() -> None:
    ours = make_async_client(json_handler(message_body()))
    theirs = anthropic.AsyncAnthropic(api_key="test-key")
    assert inspect.signature(ours.messages.create) == inspect.signature(theirs.messages.create)


def test_non_instrumented_attributes_delegate() -> None:
    client = make_client(json_handler(message_body()))
    assert client.api_key == "test-key"
    assert hasattr(client.messages, "with_raw_response")


# -- fixture replay: correct ledger rows -----------------------------------------


def test_normal_response_recorded_without_fallback() -> None:
    client = make_client(json_handler(message_body()))
    response = client.messages.create(**create_kwargs())
    assert response.model == FABLE
    with Ledger() as ledger:
        assert ledger.request_count() == 1
        assert ledger.fallback_count() == 0
        (entry,) = list(ledger.entries())
    assert entry.payload["requested_model"] == FABLE
    assert entry.payload["served_model"] == FABLE
    assert entry.payload["input_tokens"] == 100
    assert entry.payload["output_tokens"] == 50
    assert entry.payload["latency_ms"] is not None


def test_declared_fallback_recorded_with_event() -> None:
    client = make_client(json_handler(message_body(model=OPUS)))
    response = client.messages.create(**create_kwargs())
    assert response.model == OPUS  # zero behavior change for the caller
    with Ledger() as ledger:
        assert ledger.fallback_count() == 1
        row = ledger._conn.execute("SELECT * FROM fallback_events").fetchone()
    assert row["detection_method"] == "declared"
    assert row["confidence"] == 1.0
    assert row["requested_model"] == FABLE
    assert row["served_model"] == OPUS


def test_hash_mode_stores_prompt_hash_only() -> None:
    client = make_client(json_handler(message_body()))
    client.messages.create(**create_kwargs(messages=[{"role": "user", "content": "SECRET-S3"}]))
    with Ledger() as ledger:
        (entry,) = list(ledger.entries())
        path = ledger.path
    assert entry.payload["prompt_sha256"] is not None
    assert "prompt" not in entry.payload
    assert b"SECRET-S3" not in path.read_bytes()


def test_tagging_context_applies_in_metadata_mode() -> None:
    with Ledger() as ledger:
        ledger.set_privacy_mode("metadata")
    client = make_client(json_handler(message_body()))
    with switchyard.context(engagement="acme-q2", task_type="triage"):
        client.messages.create(**create_kwargs())
    client.messages.create(**create_kwargs())  # outside the block: no tags
    with Ledger() as ledger:
        tagged, untagged = list(ledger.entries())
    assert tagged.payload["engagement"] == "acme-q2"
    assert tagged.payload["task_type"] == "triage"
    assert untagged.payload["engagement"] is None


def test_nested_context_overrides_only_set_fields() -> None:
    with switchyard.context(engagement="acme-q2", task_type="triage"):
        with switchyard.context(task_type="exploit-analysis"):
            tags = switchyard.client.current_tags()
            assert tags == {"engagement": "acme-q2", "task_type": "exploit-analysis"}
        assert switchyard.client.current_tags()["task_type"] == "triage"
    assert switchyard.client.current_tags() == {}


# -- streaming --------------------------------------------------------------------


def test_create_stream_true_records_fallback_on_completion() -> None:
    client = make_client(sse_handler(OPUS))
    stream = client.messages.create(**create_kwargs(stream=True))
    events = list(stream)
    assert any(getattr(e, "type", "") == "message_stop" for e in events)
    with Ledger() as ledger:
        assert ledger.request_count() == 1
        assert ledger.fallback_count() == 1
        (entry,) = list(ledger.entries())
    assert entry.payload["served_model"] == OPUS
    assert entry.payload["output_tokens"] == 15  # from message_delta


def test_messages_stream_manager_records() -> None:
    client = make_client(sse_handler(FABLE))
    collected = []
    with client.messages.stream(**create_kwargs()) as stream:
        for text in stream.text_stream:
            collected.append(text)
    assert "".join(collected) == "Hello"
    with Ledger() as ledger:
        assert ledger.request_count() == 1
        assert ledger.fallback_count() == 0
        (entry,) = list(ledger.entries())
    assert entry.payload["served_model"] == FABLE


# -- async --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_async_create_records_fallback() -> None:
    client = make_async_client(json_handler(message_body(model=OPUS)))
    response = await client.messages.create(**create_kwargs())
    assert response.model == OPUS
    with Ledger() as ledger:
        assert ledger.request_count() == 1
        assert ledger.fallback_count() == 1


@pytest.mark.anyio
async def test_async_stream_records() -> None:
    client = make_async_client(sse_handler(OPUS))
    stream = await client.messages.create(**create_kwargs(stream=True))
    events = [event async for event in stream]
    assert events
    with Ledger() as ledger:
        assert ledger.request_count() == 1
        assert ledger.fallback_count() == 1


@pytest.mark.anyio
async def test_async_stream_manager_records() -> None:
    client = make_async_client(sse_handler(FABLE))
    async with client.messages.stream(**create_kwargs()) as stream:
        async for _ in stream:
            pass
    with Ledger() as ledger:
        assert ledger.request_count() == 1


# -- failure isolation -----------------------------------------------------------------


def test_internal_errors_never_reach_user_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = make_client(json_handler(message_body(model=OPUS)))

    def explode(self: Any) -> Any:
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr(switchyard.client.Recorder, "_resources", explode)
    response = client.messages.create(**create_kwargs())
    assert response.model == OPUS  # the user's call is untouched
    err = capsys.readouterr().err
    assert "switchyard: internal error suppressed" in err
    # logged once per process, not once per call
    client.messages.create(**create_kwargs())
    assert capsys.readouterr().err == ""


def test_stream_internal_errors_never_reach_user_code(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(sse_handler(FABLE))

    def explode(self: Any) -> Any:
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr(switchyard.client.Recorder, "_resources", explode)
    stream = client.messages.create(**create_kwargs(stream=True))
    events = list(stream)  # fully consumable despite internal failure
    assert any(getattr(e, "type", "") == "message_stop" for e in events)


def test_recorder_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = switchyard.client.Recorder()

    def explode(self: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(switchyard.client.Recorder, "_resources", explode)
    recorder.record(switchyard.client._Observed(requested_model=FABLE))  # must not raise
