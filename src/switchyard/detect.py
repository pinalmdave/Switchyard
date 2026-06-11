"""Fallback detection engine (F-DET-01, F-DET-02).

Two detectors, run in order by :class:`DetectionEngine`:

1. **Declared** (F-DET-01) — compares the requested model against what the API
   response itself declares (the ``model`` field and any served-by header).
   A mismatch is a fact, not a guess: ``confidence = 1.0``.
2. **Heuristic** (F-DET-02) — when the response declares nothing useful, compares
   latency-per-output-token and tokens/sec against rolling per-model baselines.
   Fires only when at least two signals deviate beyond a z-score threshold, and
   only after the baseline has at least 30 samples. ``confidence`` is 0.5-0.8 by
   a documented formula — never presented as a certainty.

What this module deliberately cannot do is documented in ``docs/DETECTION.md``.
LLM behavioral fingerprinting is out of scope (see docs/ROADMAP.md).
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from switchyard.ledger import switchyard_home

DECLARED = "declared"
HEURISTIC = "heuristic"

#: Response headers that may declare which model actually served the request.
SERVED_BY_HEADERS = ("anthropic-served-by", "x-served-by")

#: Heuristic defaults (see docs/DETECTION.md for the trade-offs).
DEFAULT_Z_THRESHOLD = 3.0
DEFAULT_MIN_SAMPLES = 30
MIN_HEURISTIC_CONFIDENCE = 0.5
MAX_HEURISTIC_CONFIDENCE = 0.8

_DATE_SUFFIX = re.compile(r"-20\d{6}$")


def model_family(model: str) -> str:
    """Normalize a model id by stripping a trailing date snapshot suffix.

    ``claude-fable-5-20260609`` and ``claude-fable-5`` are the same model; a
    snapshot alias must never be reported as a fallback.
    """
    return _DATE_SUFFIX.sub("", model.strip().lower())


def models_equivalent(a: str, b: str) -> bool:
    """True when two model ids refer to the same model family."""
    return model_family(a) == model_family(b)


@dataclass(frozen=True)
class ResponseObservation:
    """One observed request/response pair, SDK-agnostic.

    Capture surfaces (SDK wrapper, proxy) convert whatever they see into this
    shape; detectors never touch SDK objects directly.
    """

    requested_model: str
    response_model: str | None = None
    served_by_header: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    @classmethod
    def from_api_response(
        cls,
        requested_model: str,
        response: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
        latency_ms: float | None = None,
    ) -> ResponseObservation:
        """Build an observation from a raw Messages API response body + headers."""
        usage = response.get("usage") or {}
        served_by = None
        for name, value in (headers or {}).items():
            if name.lower() in SERVED_BY_HEADERS:
                served_by = value
                break
        return cls(
            requested_model=requested_model,
            response_model=response.get("model"),
            served_by_header=served_by,
            latency_ms=latency_ms,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of a detector. Every result carries method + confidence — always."""

    is_fallback: bool
    detection_method: str
    confidence: float
    requested_model: str
    served_model: str | None
    signals: dict[str, Any] = field(default_factory=dict)


class Detector(Protocol):
    """Detector contract — frozen at the end of Wave 1.

    Implementations return a :class:`DetectionResult` when they can say something
    about the observation, or ``None`` when they cannot judge it at all. Later
    detectors (e.g. behavioral fingerprinting on the paid platform) plug in here.
    """

    name: str

    def detect(self, observation: ResponseObservation) -> DetectionResult | None: ...


class DeclaredModelDetector:
    """F-DET-01: trust what the response itself declares about the serving model."""

    name = DECLARED

    def detect(self, observation: ResponseObservation) -> DetectionResult | None:
        """Compare requested vs declared model; ``None`` when nothing is declared."""
        requested = observation.requested_model
        for source, declared in (
            ("response.model", observation.response_model),
            ("served-by header", observation.served_by_header),
        ):
            if declared and not models_equivalent(requested, declared):
                return DetectionResult(
                    is_fallback=True,
                    detection_method=DECLARED,
                    confidence=1.0,
                    requested_model=requested,
                    served_model=declared,
                    signals={"source": source, "declared_model": declared},
                )
        if observation.response_model or observation.served_by_header:
            confirmed = observation.response_model or observation.served_by_header
            return DetectionResult(
                is_fallback=False,
                detection_method=DECLARED,
                confidence=1.0,
                requested_model=requested,
                served_model=confirmed,
                signals={"declared_model": confirmed},
            )
        return None


class BaselineStore:
    """Rolling per-model signal baselines in a local SQLite file.

    Stores Welford running statistics (count, mean, M2) per ``(model, signal)``
    so baselines survive restarts without keeping raw samples around.
    Lives in its own file (``baselines.db``) — the ledger schema stays frozen.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else switchyard_home() / "baselines.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Same thread-safety contract as Ledger: capture surfaces may record
        # from worker threads, so serialize access through one lock.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS baselines ("
                " model TEXT NOT NULL, signal TEXT NOT NULL,"
                " count INTEGER NOT NULL, mean REAL NOT NULL, m2 REAL NOT NULL,"
                " PRIMARY KEY (model, signal))"
            )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> BaselineStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def add_sample(self, model: str, signal: str, value: float) -> None:
        """Fold one sample into the running statistics (Welford update)."""
        model = model_family(model)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT count, mean, m2 FROM baselines WHERE model = ? AND signal = ?",
                (model, signal),
            ).fetchone()
            if row is None:
                count, mean, m2 = 1, value, 0.0
            else:
                count = row["count"] + 1
                delta = value - row["mean"]
                mean = row["mean"] + delta / count
                m2 = row["m2"] + delta * (value - mean)
            self._conn.execute(
                "INSERT INTO baselines (model, signal, count, mean, m2)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (model, signal) DO UPDATE SET count = ?, mean = ?, m2 = ?",
                (model, signal, count, mean, m2, count, mean, m2),
            )

    def stats(self, model: str, signal: str) -> tuple[int, float, float] | None:
        """Return ``(count, mean, sample_stddev)`` for a signal, or ``None``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT count, mean, m2 FROM baselines WHERE model = ? AND signal = ?",
                (model_family(model), signal),
            ).fetchone()
        if row is None:
            return None
        count = int(row["count"])
        std = math.sqrt(row["m2"] / (count - 1)) if count > 1 else 0.0
        return count, float(row["mean"]), std

    def sample_count(self, model: str) -> int:
        """Smallest per-signal sample count for a model (gates heuristics)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT count FROM baselines WHERE model = ?", (model_family(model),)
            ).fetchall()
        if not rows:
            return 0
        return min(int(row["count"]) for row in rows)


def _observation_signals(observation: ResponseObservation) -> dict[str, float] | None:
    """Compute the heuristic signal values for one observation, if possible."""
    if not observation.latency_ms or not observation.output_tokens:
        return None
    if observation.latency_ms <= 0 or observation.output_tokens <= 0:
        return None
    return {
        "latency_ms_per_output_token": observation.latency_ms / observation.output_tokens,
        "output_tokens_per_sec": observation.output_tokens / (observation.latency_ms / 1000.0),
    }


class HeuristicDetector:
    """F-DET-02: z-score deviation from rolling per-model baselines.

    Off until every signal has at least ``min_samples`` baseline samples.
    Fires only when **both** signals deviate beyond ``z_threshold``. The two
    signals are mathematically related (one is roughly the reciprocal of the
    other) — this is a corroboration requirement from the spec, not two
    independent witnesses; docs/DETECTION.md says so plainly.

    Confidence formula (documented, bounded):
    ``confidence = min(0.8, 0.5 + 0.05 * (z_min - z_threshold))``
    where ``z_min`` is the smallest deviating |z|.
    """

    name = HEURISTIC

    def __init__(
        self,
        baselines: BaselineStore,
        z_threshold: float = DEFAULT_Z_THRESHOLD,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> None:
        self.baselines = baselines
        self.z_threshold = z_threshold
        self.min_samples = min_samples

    def confidence_for(self, z_min: float) -> float:
        """Map the smallest deviating |z| to a confidence in [0.5, 0.8]."""
        if math.isinf(z_min):
            return MAX_HEURISTIC_CONFIDENCE
        raw = MIN_HEURISTIC_CONFIDENCE + 0.05 * (z_min - self.z_threshold)
        return max(MIN_HEURISTIC_CONFIDENCE, min(MAX_HEURISTIC_CONFIDENCE, raw))

    def detect(self, observation: ResponseObservation) -> DetectionResult | None:
        """Return a heuristic fallback result, or ``None`` (cannot judge / no trip)."""
        values = _observation_signals(observation)
        if values is None:
            return None
        model = observation.requested_model
        z_scores: dict[str, float] = {}
        for signal, value in values.items():
            stats = self.baselines.stats(model, signal)
            if stats is None or stats[0] < self.min_samples:
                return None  # baseline not ready — heuristics stay off
            _count, mean, std = stats
            if std == 0.0:
                z_scores[signal] = math.inf if value != mean else 0.0
            else:
                z_scores[signal] = abs(value - mean) / std
        deviating = {s: z for s, z in z_scores.items() if z >= self.z_threshold}
        if len(deviating) < 2:
            return None
        z_min = min(deviating.values())
        return DetectionResult(
            is_fallback=True,
            detection_method=HEURISTIC,
            confidence=round(self.confidence_for(z_min), 3),
            requested_model=model,
            served_model=None,  # heuristics cannot say *what* served the request
            signals={
                "z_scores": {
                    s: (None if math.isinf(z) else round(z, 2)) for s, z in z_scores.items()
                },
                "values": {s: round(v, 4) for s, v in values.items()},
                "z_threshold": self.z_threshold,
                "min_samples": self.min_samples,
            },
        )

    def add_baseline_sample(self, observation: ResponseObservation) -> None:
        """Fold a *declared-confirmed* observation into the rolling baselines."""
        values = _observation_signals(observation)
        if values is None:
            return
        for signal, value in values.items():
            self.baselines.add_sample(observation.requested_model, signal, value)


class DetectionEngine:
    """Runs declared detection first, heuristics only when nothing is declared.

    Baselines learn exclusively from declared-confirmed responses (the response
    said it was served by the requested model) — unverified responses never
    poison the baseline.
    """

    def __init__(
        self,
        baselines: BaselineStore | None = None,
        z_threshold: float = DEFAULT_Z_THRESHOLD,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> None:
        self.declared = DeclaredModelDetector()
        self.heuristic = HeuristicDetector(
            baselines if baselines is not None else BaselineStore(),
            z_threshold=z_threshold,
            min_samples=min_samples,
        )

    def close(self) -> None:
        """Close the baseline store."""
        self.heuristic.baselines.close()

    def __enter__(self) -> DetectionEngine:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def observe(self, observation: ResponseObservation) -> DetectionResult | None:
        """Inspect one observation; return a fallback result or ``None``.

        Side effect: declared-confirmed observations update the heuristic
        baselines for the requested model.
        """
        declared = self.declared.detect(observation)
        if declared is not None:
            if declared.is_fallback:
                return declared
            self.heuristic.add_baseline_sample(observation)
            return None
        return self.heuristic.detect(observation)
