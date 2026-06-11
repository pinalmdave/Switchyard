"""Drop-in wrapper around the official ``anthropic`` SDK (F-CLI-01).

``from switchyard import Anthropic`` — identical call signatures (the wrapper
delegates everything to the real SDK), plus: after each response, Switchyard runs
fallback detection and appends the request to the local ledger.

**The wrapper may never raise into user code.** Every Switchyard-internal
exception is caught, written to stderr once per process, and swallowed; the
user's API call succeeds or fails exactly as it would with the bare SDK.

Tagging::

    with switchyard.context(engagement="acme-q2", task_type="triage"):
        client.messages.create(...)   # events inside carry these tags
"""

from __future__ import annotations

import contextvars
import functools
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import anthropic

from switchyard.detect import DetectionEngine, ResponseObservation
from switchyard.ledger import Ledger, RequestRecord, canonical_json

__all__ = ["Anthropic", "AsyncAnthropic", "context"]

_warned_once = False


def _warn_once(exc: BaseException) -> None:
    """Log a Switchyard-internal failure to stderr, once per process."""
    global _warned_once
    if not _warned_once:
        print(
            f"switchyard: internal error suppressed (your API calls are unaffected; "
            f"events may not be recorded): {exc!r}",
            file=sys.stderr,
        )
        _warned_once = True


# -- tagging context -----------------------------------------------------------

_tags: contextvars.ContextVar[dict[str, str | None] | None] = contextvars.ContextVar(
    "switchyard_tags", default=None
)


@contextmanager
def context(engagement: str | None = None, task_type: str | None = None) -> Iterator[None]:
    """Attach ``engagement``/``task_type`` tags to every event inside the block.

    Tags are stored only in the ``metadata`` and ``full`` privacy modes; the
    default ``hash`` mode drops them (see docs/DETECTION.md and SPEC §3).
    Nested blocks override only the fields they set.
    """
    current = _tags.get() or {}
    merged = dict(current)
    if engagement is not None:
        merged["engagement"] = engagement
    if task_type is not None:
        merged["task_type"] = task_type
    token = _tags.set(merged)
    try:
        yield
    finally:
        _tags.reset(token)


def current_tags() -> dict[str, str | None]:
    """Return the active tagging context (empty dict outside any block)."""
    return dict(_tags.get() or {})


# -- recorder ------------------------------------------------------------------


@dataclass
class _Observed:
    """What the wrapper managed to see about one request/response pair."""

    requested_model: str
    response_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    prompt_repr: str | None = None


class Recorder:
    """Wires detect → ledger. Every public method is exception-proof."""

    def __init__(self, ledger: Ledger | None = None, engine: DetectionEngine | None = None) -> None:
        self._ledger = ledger
        self._engine = engine

    def _resources(self) -> tuple[Ledger, DetectionEngine]:
        if self._ledger is None:
            self._ledger = Ledger()
        if self._engine is None:
            self._engine = DetectionEngine()
        return self._ledger, self._engine

    def record(self, observed: _Observed) -> None:
        """Append the request to the ledger and record a fallback event if detected.

        Never raises — failure isolation is the wrapper's hard contract.
        """
        try:
            ledger, engine = self._resources()
            tags = current_tags()
            entry = ledger.append_request(
                RequestRecord(
                    requested_model=observed.requested_model,
                    served_model=observed.response_model,
                    prompt=observed.prompt_repr,
                    input_tokens=observed.input_tokens,
                    output_tokens=observed.output_tokens,
                    latency_ms=observed.latency_ms,
                    engagement=tags.get("engagement"),
                    task_type=tags.get("task_type"),
                )
            )
            result = engine.observe(
                ResponseObservation(
                    requested_model=observed.requested_model,
                    response_model=observed.response_model,
                    latency_ms=observed.latency_ms,
                    input_tokens=observed.input_tokens,
                    output_tokens=observed.output_tokens,
                )
            )
            if result is not None and result.is_fallback:
                ledger.record_fallback(
                    request_sequence=entry.sequence,
                    detection_method=result.detection_method,
                    confidence=result.confidence,
                    requested_model=result.requested_model,
                    served_model=result.served_model or "",
                    details=result.signals,
                )
        except Exception as exc:
            _warn_once(exc)


def _prompt_repr(kwargs: dict[str, Any]) -> str | None:
    """Canonical string of the prompt-bearing params, for hashing (or storage
    in ``full`` mode). Falls back to ``repr`` for non-JSON content."""
    parts = {k: kwargs.get(k) for k in ("system", "messages") if k in kwargs}
    if not parts:
        return None
    try:
        return canonical_json(parts)
    except (TypeError, ValueError):
        return repr(parts)


def _absorb_response(observed: _Observed, message: Any) -> None:
    """Pull model/usage off a (possibly partial) Message object, defensively."""
    model = getattr(message, "model", None)
    if model is not None:
        observed.response_model = str(model)
    usage = getattr(message, "usage", None)
    if usage is not None:
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if input_tokens is not None:
            observed.input_tokens = int(input_tokens)
        if output_tokens is not None:
            observed.output_tokens = int(output_tokens)


def _absorb_event(observed: _Observed, event: Any) -> None:
    """Accumulate model/usage from raw streaming events."""
    event_type = getattr(event, "type", None)
    if event_type == "message_start":
        _absorb_response(observed, getattr(event, "message", None))
    elif event_type == "message_delta":
        usage = getattr(event, "usage", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if output_tokens is not None:
            observed.output_tokens = int(output_tokens)


# -- streaming proxies ----------------------------------------------------------


class _InstrumentedStream:
    """Wraps a sync ``Stream`` from ``create(stream=True)``; records once done."""

    def __init__(self, stream: Any, observed: _Observed, recorder: Recorder, started: float):
        self._stream = stream
        self._observed = observed
        self._recorder = recorder
        self._started = started
        self._recorded = False

    def _finish(self) -> None:
        if self._recorded:
            return
        self._recorded = True
        self._observed.latency_ms = (time.perf_counter() - self._started) * 1000.0
        self._recorder.record(self._observed)

    def __iter__(self) -> Iterator[Any]:
        try:
            for event in self._stream:
                try:
                    _absorb_event(self._observed, event)
                except Exception as exc:
                    _warn_once(exc)
                yield event
        finally:
            self._finish()

    def __enter__(self) -> _InstrumentedStream:
        self._stream.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self._stream.__exit__(exc_type, exc, tb)
        finally:
            self._finish()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _InstrumentedAsyncStream:
    """Async twin of :class:`_InstrumentedStream`."""

    def __init__(self, stream: Any, observed: _Observed, recorder: Recorder, started: float):
        self._stream = stream
        self._observed = observed
        self._recorder = recorder
        self._started = started
        self._recorded = False

    def _finish(self) -> None:
        if self._recorded:
            return
        self._recorded = True
        self._observed.latency_ms = (time.perf_counter() - self._started) * 1000.0
        self._recorder.record(self._observed)

    async def __aiter__(self) -> AsyncIterator[Any]:
        try:
            async for event in self._stream:
                try:
                    _absorb_event(self._observed, event)
                except Exception as exc:
                    _warn_once(exc)
                yield event
        finally:
            self._finish()

    async def __aenter__(self) -> _InstrumentedAsyncStream:
        await self._stream.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self._stream.__aexit__(exc_type, exc, tb)
        finally:
            self._finish()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _InstrumentedStreamManager:
    """Wraps ``messages.stream(...)``'s context manager; records on exit using
    the stream's accumulated message snapshot (partial if the caller broke out)."""

    def __init__(self, manager: Any, observed: _Observed, recorder: Recorder, started: float):
        self._manager = manager
        self._observed = observed
        self._recorder = recorder
        self._started = started
        self._stream: Any = None

    def __enter__(self) -> Any:
        self._stream = self._manager.__enter__()
        return self._stream

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        try:
            try:
                snapshot = getattr(self._stream, "current_message_snapshot", None)
                if snapshot is not None:
                    _absorb_response(self._observed, snapshot)
                self._observed.latency_ms = (time.perf_counter() - self._started) * 1000.0
                self._recorder.record(self._observed)
            except Exception as inner:
                _warn_once(inner)
        finally:
            result = self._manager.__exit__(exc_type, exc, tb)
        return bool(result) if result is not None else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)


class _InstrumentedAsyncStreamManager:
    """Async twin of :class:`_InstrumentedStreamManager`."""

    def __init__(self, manager: Any, observed: _Observed, recorder: Recorder, started: float):
        self._manager = manager
        self._observed = observed
        self._recorder = recorder
        self._started = started
        self._stream: Any = None

    async def __aenter__(self) -> Any:
        self._stream = await self._manager.__aenter__()
        return self._stream

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        try:
            try:
                snapshot = getattr(self._stream, "current_message_snapshot", None)
                if snapshot is not None:
                    _absorb_response(self._observed, snapshot)
                self._observed.latency_ms = (time.perf_counter() - self._started) * 1000.0
                self._recorder.record(self._observed)
            except Exception as inner:
                _warn_once(inner)
        finally:
            result = await self._manager.__aexit__(exc_type, exc, tb)
        return bool(result) if result is not None else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)


# -- messages proxies -------------------------------------------------------------


class _MessagesProxy:
    """Delegates everything to the real ``Messages`` resource; instruments
    ``create`` and ``stream``. ``functools.wraps`` preserves the SDK signatures."""

    def __init__(self, wrapped: Any, recorder: Recorder) -> None:
        self._wrapped = wrapped
        self._recorder = recorder

        @functools.wraps(wrapped.create)
        def create(*args: Any, **kwargs: Any) -> Any:
            return self._instrumented_create(*args, **kwargs)

        self.create: Callable[..., Any] = create

        @functools.wraps(wrapped.stream)
        def stream(*args: Any, **kwargs: Any) -> Any:
            return self._instrumented_stream(*args, **kwargs)

        self.stream: Callable[..., Any] = stream

    def _observed_for(self, kwargs: dict[str, Any]) -> _Observed:
        return _Observed(
            requested_model=str(kwargs.get("model", "")),
            prompt_repr=_prompt_repr(kwargs),
        )

    def _instrumented_create(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        response = self._wrapped.create(*args, **kwargs)
        try:
            observed = self._observed_for(kwargs)
        except Exception as exc:
            _warn_once(exc)
            return response
        if kwargs.get("stream"):
            return _InstrumentedStream(response, observed, self._recorder, started)
        try:
            _absorb_response(observed, response)
            observed.latency_ms = (time.perf_counter() - started) * 1000.0
        except Exception as exc:
            _warn_once(exc)
        self._recorder.record(observed)
        return response

    def _instrumented_stream(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        manager = self._wrapped.stream(*args, **kwargs)
        try:
            observed = self._observed_for(kwargs)
        except Exception as exc:
            _warn_once(exc)
            return manager
        return _InstrumentedStreamManager(manager, observed, self._recorder, started)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


class _AsyncMessagesProxy:
    """Async twin of :class:`_MessagesProxy`."""

    def __init__(self, wrapped: Any, recorder: Recorder) -> None:
        self._wrapped = wrapped
        self._recorder = recorder

        @functools.wraps(wrapped.create)
        async def create(*args: Any, **kwargs: Any) -> Any:
            return await self._instrumented_create(*args, **kwargs)

        self.create: Callable[..., Any] = create

        @functools.wraps(wrapped.stream)
        def stream(*args: Any, **kwargs: Any) -> Any:
            return self._instrumented_stream(*args, **kwargs)

        self.stream: Callable[..., Any] = stream

    def _observed_for(self, kwargs: dict[str, Any]) -> _Observed:
        return _Observed(
            requested_model=str(kwargs.get("model", "")),
            prompt_repr=_prompt_repr(kwargs),
        )

    async def _instrumented_create(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        response = await self._wrapped.create(*args, **kwargs)
        try:
            observed = self._observed_for(kwargs)
        except Exception as exc:
            _warn_once(exc)
            return response
        if kwargs.get("stream"):
            return _InstrumentedAsyncStream(response, observed, self._recorder, started)
        try:
            _absorb_response(observed, response)
            observed.latency_ms = (time.perf_counter() - started) * 1000.0
        except Exception as exc:
            _warn_once(exc)
        self._recorder.record(observed)
        return response

    def _instrumented_stream(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        manager = self._wrapped.stream(*args, **kwargs)
        try:
            observed = self._observed_for(kwargs)
        except Exception as exc:
            _warn_once(exc)
            return manager
        return _InstrumentedAsyncStreamManager(manager, observed, self._recorder, started)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


# -- public clients ----------------------------------------------------------------


class Anthropic(anthropic.Anthropic):
    """Drop-in replacement for :class:`anthropic.Anthropic` with fallback auditing.

    Identical constructor and call signatures. Two optional extras:
    ``switchyard_ledger`` and ``switchyard_engine`` inject preconfigured
    instances (used by tests; defaults are created lazily on first record).
    """

    def __init__(
        self,
        *args: Any,
        switchyard_ledger: Ledger | None = None,
        switchyard_engine: DetectionEngine | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        recorder = Recorder(switchyard_ledger, switchyard_engine)
        self.messages = _MessagesProxy(self.messages, recorder)  # type: ignore[assignment, misc]


class AsyncAnthropic(anthropic.AsyncAnthropic):
    """Drop-in replacement for :class:`anthropic.AsyncAnthropic` with fallback auditing."""

    def __init__(
        self,
        *args: Any,
        switchyard_ledger: Ledger | None = None,
        switchyard_engine: DetectionEngine | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        recorder = Recorder(switchyard_ledger, switchyard_engine)
        self.messages = _AsyncMessagesProxy(self.messages, recorder)  # type: ignore[assignment, misc]
