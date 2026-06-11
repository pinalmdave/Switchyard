"""Detection engine tests (F-DET-01/02) against recorded fixtures."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from switchyard.detect import (
    DECLARED,
    DEFAULT_MIN_SAMPLES,
    HEURISTIC,
    MAX_HEURISTIC_CONFIDENCE,
    MIN_HEURISTIC_CONFIDENCE,
    BaselineStore,
    DeclaredModelDetector,
    DetectionEngine,
    DetectionResult,
    HeuristicDetector,
    ResponseObservation,
    model_family,
    models_equivalent,
)

FIXTURES = Path(__file__).parent / "fixtures"
FABLE = "claude-fable-5"


def load_observations(name: str) -> list[ResponseObservation]:
    data = json.loads((FIXTURES / name).read_text())
    return [
        ResponseObservation.from_api_response(
            requested_model=obs["requested_model"],
            response=obs["response"],
            headers=obs.get("headers"),
            latency_ms=obs.get("latency_ms"),
        )
        for obs in data["observations"]
    ]


def normal_observation(latency_ms: float = 8000.0, output_tokens: int = 400) -> ResponseObservation:
    """A declared-confirmed observation (feeds the baseline)."""
    return ResponseObservation(
        requested_model=FABLE,
        response_model=FABLE,
        latency_ms=latency_ms,
        input_tokens=500,
        output_tokens=output_tokens,
    )


def ambiguous_observation(
    latency_ms: float = 8000.0, output_tokens: int = 400
) -> ResponseObservation:
    """An observation that declares nothing (only heuristics can judge it)."""
    return ResponseObservation(
        requested_model=FABLE,
        response_model=None,
        latency_ms=latency_ms,
        input_tokens=500,
        output_tokens=output_tokens,
    )


def assert_well_formed(result: DetectionResult) -> None:
    """Test gate: no event without method + confidence."""
    assert result.detection_method in (DECLARED, HEURISTIC)
    assert 0.0 <= result.confidence <= 1.0


# -- model normalization -------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("claude-fable-5", "claude-fable-5"),
        ("claude-fable-5-20260609", "claude-fable-5"),
        ("claude-opus-4-8-20260120", "claude-opus-4-8"),
        ("  Claude-Fable-5  ", "claude-fable-5"),
    ],
)
def test_model_family(model: str, family: str) -> None:
    assert model_family(model) == family


def test_models_equivalent() -> None:
    assert models_equivalent("claude-fable-5", "claude-fable-5-20260609")
    assert not models_equivalent("claude-fable-5", "claude-opus-4-8")


# -- declared detection (F-DET-01) ----------------------------------------------


def test_declared_fixtures_all_detected_with_certainty() -> None:
    """Precision/recall 1.0 on the declared-fallback fixture set."""
    detector = DeclaredModelDetector()
    for observation in load_observations("declared_fallback.json"):
        result = detector.detect(observation)
        assert result is not None, observation
        assert_well_formed(result)
        assert result.is_fallback
        assert result.detection_method == DECLARED
        assert result.confidence == 1.0
        assert result.served_model is not None


def test_normal_fixtures_produce_no_fallbacks() -> None:
    detector = DeclaredModelDetector()
    for observation in load_observations("normal.json"):
        result = detector.detect(observation)
        assert result is not None, observation
        assert not result.is_fallback
        assert result.confidence == 1.0


def test_ambiguous_fixtures_get_no_declared_verdict() -> None:
    detector = DeclaredModelDetector()
    for observation in load_observations("ambiguous.json"):
        assert detector.detect(observation) is None


def test_from_api_response_extracts_served_by_header() -> None:
    observation = ResponseObservation.from_api_response(
        requested_model=FABLE,
        response={"model": None, "usage": {"input_tokens": 10, "output_tokens": 20}},
        headers={"Anthropic-Served-By": "claude-opus-4-8"},
        latency_ms=1000.0,
    )
    assert observation.served_by_header == "claude-opus-4-8"
    assert observation.output_tokens == 20


# -- baseline store --------------------------------------------------------------


def test_baseline_store_welford_matches_population(tmp_path: Path) -> None:
    samples = [10.0, 12.0, 9.5, 11.2, 10.8, 13.1]
    with BaselineStore(tmp_path / "b.db") as store:
        for value in samples:
            store.add_sample(FABLE, "latency_ms_per_output_token", value)
        stats = store.stats(FABLE, "latency_ms_per_output_token")
    assert stats is not None
    count, mean, std = stats
    assert count == len(samples)
    assert mean == pytest.approx(sum(samples) / len(samples))
    expected_var = sum((s - mean) ** 2 for s in samples) / (len(samples) - 1)
    assert std == pytest.approx(math.sqrt(expected_var))


def test_baseline_store_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "b.db"
    with BaselineStore(path) as store:
        store.add_sample(FABLE, "output_tokens_per_sec", 50.0)
    with BaselineStore(path) as store:
        stats = store.stats(FABLE, "output_tokens_per_sec")
    assert stats is not None
    assert stats[0] == 1


def test_baseline_store_normalizes_model_aliases(tmp_path: Path) -> None:
    with BaselineStore(tmp_path / "b.db") as store:
        store.add_sample("claude-fable-5-20260609", "output_tokens_per_sec", 50.0)
        stats = store.stats("claude-fable-5", "output_tokens_per_sec")
        assert stats is not None
        assert store.sample_count("claude-fable-5") == 1
        assert store.sample_count("claude-haiku-4-5") == 0


# -- heuristic detection (F-DET-02) ----------------------------------------------


def seeded_engine(tmp_path: Path, n: int = DEFAULT_MIN_SAMPLES) -> DetectionEngine:
    """Engine whose baseline holds n declared-confirmed samples around 20ms/token."""
    engine = DetectionEngine(BaselineStore(tmp_path / "baselines.db"))
    for i in range(n):
        # mild jitter so the baseline has non-zero variance
        engine.observe(normal_observation(latency_ms=8000.0 + (i % 7) * 120.0))
    return engine


def test_heuristic_off_until_min_samples(tmp_path: Path) -> None:
    engine = seeded_engine(tmp_path, n=DEFAULT_MIN_SAMPLES - 1)
    outlier = ambiguous_observation(latency_ms=120_000.0)
    assert engine.observe(outlier) is None
    engine.close()


def test_heuristic_fires_on_strong_outlier_after_baseline(tmp_path: Path) -> None:
    engine = seeded_engine(tmp_path)
    result = engine.observe(ambiguous_observation(latency_ms=120_000.0))
    assert result is not None
    assert_well_formed(result)
    assert result.is_fallback
    assert result.detection_method == HEURISTIC
    assert MIN_HEURISTIC_CONFIDENCE <= result.confidence <= MAX_HEURISTIC_CONFIDENCE
    assert result.served_model is None  # heuristics never claim to know the server
    assert "z_scores" in result.signals
    engine.close()


def test_heuristic_silent_on_normal_ambiguous_traffic(tmp_path: Path) -> None:
    engine = seeded_engine(tmp_path)
    assert engine.observe(ambiguous_observation(latency_ms=8100.0)) is None
    engine.close()


def test_heuristic_needs_timing_data(tmp_path: Path) -> None:
    engine = seeded_engine(tmp_path)
    no_latency = ResponseObservation(requested_model=FABLE, output_tokens=400)
    no_tokens = ResponseObservation(requested_model=FABLE, latency_ms=8000.0, output_tokens=0)
    assert engine.observe(no_latency) is None
    assert engine.observe(no_tokens) is None
    engine.close()


def test_declared_fallback_never_updates_baseline(tmp_path: Path) -> None:
    with BaselineStore(tmp_path / "b.db") as store:
        engine = DetectionEngine(store)
        fallback = ResponseObservation(
            requested_model=FABLE,
            response_model="claude-opus-4-8",
            latency_ms=5000.0,
            output_tokens=300,
        )
        result = engine.observe(fallback)
        assert result is not None
        assert result.is_fallback
        assert store.sample_count(FABLE) == 0


def test_ambiguous_traffic_never_updates_baseline(tmp_path: Path) -> None:
    with BaselineStore(tmp_path / "b.db") as store:
        engine = DetectionEngine(store)
        engine.observe(ambiguous_observation())
        assert store.sample_count(FABLE) == 0


@given(z_min=st.floats(min_value=0.0, max_value=1e6, allow_nan=False))
def test_heuristic_confidence_always_bounded(z_min: float) -> None:
    detector = HeuristicDetector(baselines=None)  # type: ignore[arg-type]  # math only
    confidence = detector.confidence_for(z_min)
    assert MIN_HEURISTIC_CONFIDENCE <= confidence <= MAX_HEURISTIC_CONFIDENCE


def test_heuristic_confidence_saturates_at_documented_points() -> None:
    detector = HeuristicDetector(baselines=None)  # type: ignore[arg-type]  # math only
    assert detector.confidence_for(3.0) == pytest.approx(0.5)
    assert detector.confidence_for(9.0) == pytest.approx(0.8)
    assert detector.confidence_for(math.inf) == 0.8


# -- engine end-to-end over all fixtures -------------------------------------------


def test_engine_over_all_fixtures_every_event_is_well_formed(tmp_path: Path) -> None:
    engine = DetectionEngine(BaselineStore(tmp_path / "baselines.db"))
    results: list[DetectionResult] = []
    all_fixtures: list[ResponseObservation] = (
        load_observations("normal.json")
        + load_observations("declared_fallback.json")
        + load_observations("ambiguous.json")
    )
    for observation in all_fixtures:
        result = engine.observe(observation)
        if result is not None:
            results.append(result)
    # exactly the declared-fallback fixtures fire; ambiguous ones stay silent
    # (baseline has far fewer than 30 samples)
    expected = len(load_observations("declared_fallback.json"))
    assert len(results) == expected
    for result in results:
        assert_well_formed(result)
        assert result.detection_method == DECLARED
    engine.close()
