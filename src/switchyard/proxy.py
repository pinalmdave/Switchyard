"""Local proxy mode (F-PRX-01) — optional extra: ``switchyard-ai[proxy]``.

``switchyard proxy --port 4140`` starts a localhost-only passthrough to
``api.anthropic.com``. Point any tool at it with
``ANTHROPIC_BASE_URL=http://127.0.0.1:4140`` and Switchyard captures the
traffic that can't change imports (Claude Code itself, LangChain apps, ...).

Guarantees:

- **Byte-for-byte passthrough.** Response bytes (including SSE streams) are
  relayed exactly as received — Switchyard tees a bounded copy for detection
  but never alters, reorders, or delays the bytes on the wire.
- **Loopback only.** :func:`run_proxy` refuses to bind anything but loopback.
- **Off the response path.** Detection and ledger writes happen in a Starlette
  background task after the response has been fully sent.
- API keys pass through to the configured upstream untouched; they are never
  logged and never written to disk (the ledger stores no headers).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

try:
    import uvicorn
    from starlette.applications import Starlette
    from starlette.background import BackgroundTask
    from starlette.requests import Request
    from starlette.responses import Response, StreamingResponse
    from starlette.routing import Route
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "Proxy mode needs the optional extra: pip install 'switchyard-ai[proxy]'"
    ) from exc

import httpx

from switchyard.client import Recorder, _Observed, _warn_once

DEFAULT_UPSTREAM = "https://api.anthropic.com"
DEFAULT_PORT = 4140
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

#: Detection only ever needs the head of the body; stop teeing beyond this.
MAX_TEE_BYTES = 4 * 1024 * 1024

#: Hop-by-hop headers that must not be forwarded in either direction.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _forward_request_headers(headers: Any) -> dict[str, str]:
    return {
        k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }


def _forward_response_headers(headers: Any) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _observed_from_request_body(body: bytes) -> _Observed:
    """Extract requested model + prompt repr from the outbound request body."""
    requested_model = ""
    prompt_repr: str | None = None
    try:
        payload = json.loads(body)
        requested_model = str(payload.get("model", ""))
        parts = {k: payload.get(k) for k in ("system", "messages") if k in payload}
        if parts:
            prompt_repr = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError, AttributeError):
        pass
    return _Observed(requested_model=requested_model, prompt_repr=prompt_repr)


def _absorb_response_bytes(observed: _Observed, content_type: str, body: bytes) -> None:
    """Pull model/usage out of a buffered (possibly truncated) response body."""
    text = body.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            _absorb_event_dict(observed, event)
    else:
        try:
            message = json.loads(text)
        except ValueError:
            return
        _absorb_message_dict(observed, message)


def _absorb_message_dict(observed: _Observed, message: dict[str, Any]) -> None:
    model = message.get("model")
    if model:
        observed.response_model = str(model)
    usage = message.get("usage") or {}
    if usage.get("input_tokens") is not None:
        observed.input_tokens = int(usage["input_tokens"])
    if usage.get("output_tokens") is not None:
        observed.output_tokens = int(usage["output_tokens"])


def _absorb_event_dict(observed: _Observed, event: dict[str, Any]) -> None:
    event_type = event.get("type")
    if event_type == "message_start":
        _absorb_message_dict(observed, event.get("message") or {})
    elif event_type == "message_delta":
        usage = event.get("usage") or {}
        if usage.get("output_tokens") is not None:
            observed.output_tokens = int(usage["output_tokens"])


def create_app(
    upstream: str = DEFAULT_UPSTREAM,
    http_client: httpx.AsyncClient | None = None,
    recorder: Recorder | None = None,
) -> Starlette:
    """Build the passthrough ASGI app (testable without a server).

    ``http_client`` and ``recorder`` are injection points for tests; defaults
    talk to the real upstream and record to the default ledger.
    """
    client = http_client if http_client is not None else httpx.AsyncClient(timeout=600.0)
    rec = recorder if recorder is not None else Recorder()
    base = upstream.rstrip("/")

    async def passthrough(request: Request) -> Response:
        body = await request.body()
        url = base + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        started = time.perf_counter()
        upstream_request = client.build_request(
            request.method,
            url,
            headers=_forward_request_headers(request.headers),
            content=body,
        )
        upstream_response = await client.send(upstream_request, stream=True)
        content_type = upstream_response.headers.get("content-type", "")
        tee = bytearray()

        async def relay() -> AsyncIterator[bytes]:
            try:
                if upstream_response.is_stream_consumed:
                    # Pre-loaded body (mock transports, cached responses):
                    # there is nothing left to stream, relay it whole.
                    chunk = upstream_response.content
                    tee.extend(chunk[:MAX_TEE_BYTES])
                    yield chunk
                else:
                    async for chunk in upstream_response.aiter_raw():
                        if len(tee) < MAX_TEE_BYTES:
                            tee.extend(chunk[: MAX_TEE_BYTES - len(tee)])
                        yield chunk
            finally:
                await upstream_response.aclose()

        def record() -> None:
            # Runs as a BackgroundTask: after the response has been sent,
            # never on the byte path.
            try:
                observed = _observed_from_request_body(body)
                observed.latency_ms = (time.perf_counter() - started) * 1000.0
                _absorb_response_bytes(observed, content_type, bytes(tee))
                if observed.requested_model:
                    rec.record(observed)
            except Exception as exc:
                _warn_once(exc)

        return StreamingResponse(
            relay(),
            status_code=upstream_response.status_code,
            headers=_forward_response_headers(upstream_response.headers),
            background=BackgroundTask(record),
        )

    return Starlette(
        routes=[
            Route(
                "/{path:path}",
                passthrough,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            )
        ]
    )


def run_proxy(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    upstream: str = DEFAULT_UPSTREAM,
) -> None:
    """Start the proxy with uvicorn. Refuses to bind non-loopback addresses."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"switchyard proxy binds loopback only (got {host!r}); "
            "exposing your API traffic to the network is out of the question"
        )
    uvicorn.run(create_app(upstream=upstream), host=host, port=port, log_level="warning")
