"""CI gate (F-RPT-03): ``switchyard check --max-rate 0.02 --since 24h``.

Exits non-zero when the observed fallback rate breaches the threshold, so a
pipeline can fail loudly instead of silently shipping work done on the wrong
model. See ``examples/ci_gate.yml`` for the GitHub Actions snippet.
"""

from __future__ import annotations

from dataclasses import dataclass

from switchyard.ledger import Ledger
from switchyard.report import build_report


def parse_rate(value: str) -> float:
    """Parse ``0.02`` or ``2%`` into a fraction in [0, 1]."""
    text = value.strip()
    try:
        rate = float(text[:-1]) / 100.0 if text.endswith("%") else float(text)
    except ValueError:
        raise ValueError(f"invalid rate {value!r}; use e.g. 0.02 or 2%") from None
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"rate must be between 0 and 1 (got {rate})")
    return rate


@dataclass(frozen=True)
class CheckResult:
    """Outcome of the CI gate."""

    ok: bool
    fallback_rate: float
    max_rate: float
    total_requests: int
    fallback_events: int
    since: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "fallback_rate": round(self.fallback_rate, 4),
            "max_rate": self.max_rate,
            "total_requests": self.total_requests,
            "fallback_events": self.fallback_events,
            "since": self.since,
        }


def run_check(ledger: Ledger, max_rate: float, since: str | None = None) -> CheckResult:
    """Compare the observed fallback rate against ``max_rate``.

    A window with zero requests passes (rate 0) — an empty ledger is a capture
    problem, not a fallback problem; ``switchyard audit`` is the tool for that.
    """
    report = build_report(ledger, since=since)
    return CheckResult(
        ok=report.fallback_rate <= max_rate,
        fallback_rate=report.fallback_rate,
        max_rate=max_rate,
        total_requests=report.total_requests,
        fallback_events=report.fallback_events,
        since=since,
    )
