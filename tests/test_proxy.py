"""Proxy mode tests (F-PRX-01): byte parity, loopback enforcement, latency budget."""

from __future__ import annotations

import json
import statistics
import time
from typing import Any

import httpx
import pytest

pytest.importorskip("starlette")
pytest.importorskip("uvicorn")

from starlette.testclient import TestClient

import switchyard.proxy as proxy_module
from helpers import FABLE, OPUS, message_body, sse_body
from switchyard.ledger import Ledger
from switchyard.proxy import create_app, run_proxy


def upstream_transport(
    body: bytes | dict[str, Any],
    content_type: str = "application/json",
    extra_headers: dict[str, str] | None = None,
    capture: dict[str, Any] | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["request"] = request
        if isinstance(body, dict):
            return httpx.Response(200, json=body, headers=extra_headers or {})
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": content_type, **(extra_headers or {})},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def proxy_client(upstream_client: httpx.AsyncClient) -> TestClient:
    app = create_app(upstream="https://api.anthropic.com", http_client=upstream_client)
    return TestClient(app)


def messages_request(
    client: TestClient, model: str = FABLE, stream: bool = False
) -> httpx.Response:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "triage this CVE"}],
    }
    if stream:
        payload["stream"] = True
    return client.post("/v1/messages", json=payload, headers={"x-api-key": "sk-test"})


# -- passthrough parity ----------------------------------------------------------


def test_sse_stream_passes_through_byte_for_byte() -> None:
    raw = sse_body(FABLE)
    client = proxy_client(upstream_transport(raw, content_type="text/event-stream"))
    response = messages_request(client, stream=True)
    assert response.status_code == 200
    assert response.content == raw  # exact bytes, nothing injected or reordered
    assert response.headers["content-type"] == "text/event-stream"


def test_json_response_passes_through_byte_for_byte() -> None:
    body = message_body()
    raw = json.dumps(body).encode()
    client = proxy_client(upstream_transport(raw, content_type="application/json"))
    response = messages_request(client)
    assert response.content == raw


def test_response_headers_preserved_and_hop_by_hop_stripped() -> None:
    client = proxy_client(
        upstream_transport(
            message_body(),
            extra_headers={"anthropic-served-by": FABLE, "transfer-encoding": "chunked"},
        )
    )
    response = messages_request(client)
    assert response.headers["anthropic-served-by"] == FABLE
    assert "transfer-encoding" not in response.headers


def test_request_headers_forwarded_without_host() -> None:
    capture: dict[str, Any] = {}
    client = proxy_client(upstream_transport(message_body(), capture=capture))
    messages_request(client)
    forwarded: httpx.Request = capture["request"]
    assert forwarded.headers["x-api-key"] == "sk-test"
    assert forwarded.url.host == "api.anthropic.com"  # host rewritten to upstream
    assert forwarded.url.path == "/v1/messages"


def test_non_messages_paths_pass_through() -> None:
    client = proxy_client(upstream_transport({"data": []}))
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json() == {"data": []}
    with Ledger() as ledger:  # no model in request body -> nothing recorded
        assert ledger.request_count() == 0


# -- detection off the response path -----------------------------------------------


def test_fallback_json_response_lands_in_ledger() -> None:
    client = proxy_client(upstream_transport(message_body(model=OPUS)))
    messages_request(client)
    with Ledger() as ledger:
        assert ledger.request_count() == 1
        assert ledger.fallback_count() == 1
        (entry,) = list(ledger.entries())
    assert entry.payload["requested_model"] == FABLE
    assert entry.payload["served_model"] == OPUS
    assert entry.payload["input_tokens"] == 100
    assert entry.payload["prompt_sha256"] is not None


def test_fallback_sse_stream_lands_in_ledger() -> None:
    client = proxy_client(upstream_transport(sse_body(OPUS), content_type="text/event-stream"))
    messages_request(client, stream=True)
    with Ledger() as ledger:
        assert ledger.request_count() == 1
        assert ledger.fallback_count() == 1
        (entry,) = list(ledger.entries())
    assert entry.payload["served_model"] == OPUS
    assert entry.payload["output_tokens"] == 15


def test_multiple_requests_all_recorded() -> None:
    """Regression: background tasks run in varying worker threads; every
    request must still land in the (shared, thread-safe) ledger."""
    client = proxy_client(upstream_transport(message_body(model=OPUS)))
    for _ in range(3):
        messages_request(client)
    with Ledger() as ledger:
        assert ledger.request_count() == 3
        assert ledger.fallback_count() == 3
        assert ledger.verify().ok


def test_normal_traffic_recorded_without_event() -> None:
    client = proxy_client(upstream_transport(message_body()))
    messages_request(client)
    with Ledger() as ledger:
        assert ledger.request_count() == 1
        assert ledger.fallback_count() == 0


def test_recording_failure_never_breaks_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    import switchyard.client

    monkeypatch.setattr(switchyard.client, "_warned_once", False)

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr(proxy_module, "_observed_from_request_body", explode)
    client = proxy_client(upstream_transport(message_body(model=OPUS)))
    response = messages_request(client)
    assert response.status_code == 200
    assert response.json()["model"] == OPUS


# -- loopback enforcement ------------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com"])
def test_run_proxy_refuses_non_loopback(host: str) -> None:
    with pytest.raises(ValueError, match="loopback only"):
        run_proxy(host=host)


def test_run_proxy_binds_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        calls.update(kwargs)

    monkeypatch.setattr(proxy_module.uvicorn, "run", fake_run)
    run_proxy(host="127.0.0.1", port=4140)
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 4140


def test_cli_proxy_requires_extra_or_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from switchyard.cli import main

    monkeypatch.setattr(proxy_module.uvicorn, "run", lambda app, **kwargs: None)
    result = CliRunner().invoke(main, ["proxy", "--port", "4141"])
    assert result.exit_code == 0
    assert "ANTHROPIC_BASE_URL" in result.output


def test_cli_proxy_rejects_non_loopback_host() -> None:
    from click.testing import CliRunner

    from switchyard.cli import main

    result = CliRunner().invoke(main, ["proxy", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "loopback" in result.output


# -- latency budget -------------------------------------------------------------------


def test_proxy_overhead_p99_under_threshold() -> None:
    """F-PRX-01 asks <5ms p99 added locally; CI threshold is deliberately generous."""
    client = proxy_client(upstream_transport(message_body()))
    timings: list[float] = []
    messages_request(client)  # warm-up
    for _ in range(50):
        started = time.perf_counter()
        messages_request(client)
        timings.append((time.perf_counter() - started) * 1000.0)
    p99 = statistics.quantiles(timings, n=100)[98]
    assert p99 < 250.0, f"p99 proxy round-trip {p99:.1f}ms exceeds the generous CI budget"
