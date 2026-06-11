"""Switchyard — detect silent Claude model fallbacks and log them locally.

Public API (grows session by session, see SESSIONS.md):

- :data:`__version__`
- :class:`switchyard.ledger.Ledger` and friends (hash-chained local ledger)
- :class:`switchyard.detect.DetectionEngine` and the :class:`switchyard.detect.Detector`
  protocol (fallback detection)

The wrapped ``Anthropic`` client and ``audit`` entry points arrive with the
capture-surface and reporting sessions.
"""

from switchyard.detect import (
    DetectionEngine,
    DetectionResult,
    Detector,
    ResponseObservation,
)
from switchyard.ledger import (
    Ledger,
    LedgerEntry,
    PrivacyMode,
    RequestRecord,
    VerifyResult,
)

__version__ = "0.1.0"

__all__ = [
    "DetectionEngine",
    "DetectionResult",
    "Detector",
    "Ledger",
    "LedgerEntry",
    "PrivacyMode",
    "RequestRecord",
    "ResponseObservation",
    "VerifyResult",
    "__version__",
]
