"""Runnable demo of Switchyard proxy mode — fully offline.

Builds the passthrough app with a mocked upstream (one Fable 5 response, one
silent Opus 4.8 fallback), sends two requests through it the way any
ANTHROPIC_BASE_URL-pointed tool would, and shows what landed in the ledger.

Run:  uv run python examples/proxy_mode.py
(Set SWITCHYARD_HOME to control where the ledger is written.)

Against the real API you would instead run:
    switchyard proxy --port 4140
    export ANTHROPIC_BASE_URL=http://127.0.0.1:4140
"""

from __future__ import annotations

from itertools import count

import httpx
from starlette.testclient import TestClient

from switchyard.ledger import Ledger
from switchyard.proxy import create_app

FABLE = "claude-fable-5"
OPUS = "claude-opus-4-8"

SERVED_MODELS = [FABLE, OPUS]
_counter = count()


def fake_upstream(request: httpx.Request) -> httpx.Response:
    served = SERVED_MODELS[next(_counter) % len(SERVED_MODELS)]
    return httpx.Response(
        200,
        json={
            "id": "msg_proxy_demo",
            "type": "message",
            "role": "assistant",
            "model": served,
            "content": [{"type": "text", "text": f"(recorded response from {served})"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 280, "output_tokens": 365},
        },
    )


def main() -> None:
    app = create_app(
        upstream="https://api.anthropic.com",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_upstream)),
    )
    via_proxy = TestClient(app)

    for i in range(2):
        response = via_proxy.post(
            "/v1/messages",
            json={
                "model": FABLE,
                "max_tokens": 512,
                "messages": [{"role": "user", "content": f"triage finding #{i + 1}"}],
            },
            headers={"x-api-key": "offline-demo"},
        )
        print(f"request {i + 1}: asked for {FABLE}, served by {response.json()['model']}")

    with Ledger() as ledger:
        print(f"\nledger: {ledger.path}")
        print(f"requests recorded:  {ledger.request_count()}")
        print(f"fallback events:    {ledger.fallback_count()}")
        result = ledger.verify()
        print(f"chain verified:     {result.ok} ({result.entries_checked} entries)")


if __name__ == "__main__":
    main()
