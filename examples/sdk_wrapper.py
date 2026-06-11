"""Runnable demo of the Switchyard SDK wrapper — fully offline.

Replays recorded responses (one served by the requested Fable 5, one silently
served by Opus 4.8) through the real ``anthropic`` SDK via an httpx mock
transport, then shows what landed in the ledger.

Run:  uv run python examples/sdk_wrapper.py
(Set SWITCHYARD_HOME to control where the ledger is written.)

To use the wrapper against the live API, the only change to your code is the
import — ``from switchyard import Anthropic`` instead of
``from anthropic import Anthropic``.
"""

from __future__ import annotations

import json
from itertools import count

import httpx

import switchyard
from switchyard.ledger import Ledger

FABLE = "claude-fable-5"
OPUS = "claude-opus-4-8"

# First response declares the requested model; second silently declares Opus.
SERVED_MODELS = [FABLE, OPUS]
_counter = count()


def replay_handler(request: httpx.Request) -> httpx.Response:
    served = SERVED_MODELS[next(_counter) % len(SERVED_MODELS)]
    body = {
        "id": "msg_demo",
        "type": "message",
        "role": "assistant",
        "model": served,
        "content": [{"type": "text", "text": f"(recorded response from {served})"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 320, "output_tokens": 410},
    }
    return httpx.Response(200, json=body)


def main() -> None:
    client = switchyard.Anthropic(
        api_key="offline-demo",
        http_client=httpx.Client(transport=httpx.MockTransport(replay_handler)),
    )

    with switchyard.context(engagement="demo", task_type="triage"):
        for i in range(2):
            message = client.messages.create(
                model=FABLE,
                max_tokens=512,
                messages=[{"role": "user", "content": f"triage finding #{i + 1}"}],
            )
            print(f"request {i + 1}: asked for {FABLE}, served by {message.model}")

    with Ledger() as ledger:
        print(f"\nledger: {ledger.path}")
        print(f"requests recorded:  {ledger.request_count()}")
        print(f"fallback events:    {ledger.fallback_count()}")
        result = ledger.verify()
        print(f"chain verified:     {result.ok} ({result.entries_checked} entries)")
        events = ledger._conn.execute(
            "SELECT detection_method, confidence, requested_model, served_model"
            " FROM fallback_events"
        ).fetchall()
        for event in events:
            print(
                "fallback detected:  "
                + json.dumps(
                    {
                        "method": event["detection_method"],
                        "confidence": event["confidence"],
                        "requested": event["requested_model"],
                        "served": event["served_model"],
                    }
                )
            )


if __name__ == "__main__":
    main()
