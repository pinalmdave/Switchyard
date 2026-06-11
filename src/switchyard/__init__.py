"""Switchyard — detect silent Claude model fallbacks and log them locally.

Public API (grows session by session, see SESSIONS.md):

- :class:`switchyard.client.Anthropic` / :class:`switchyard.client.AsyncAnthropic` —
  drop-in wrapped SDK clients — and :func:`switchyard.client.context` for tagging
- :data:`__version__`
- :class:`switchyard.ledger.Ledger` and friends (hash-chained local ledger)
- :class:`switchyard.detect.DetectionEngine` and the :class:`switchyard.detect.Detector`
  protocol (fallback detection)

The ``audit`` entry point arrives with the reporting session.
"""

from switchyard.client import Anthropic, AsyncAnthropic, context
from switchyard.detect import (
    DetectionEngine,
    DetectionResult,
    Detector,
    ResponseObservation,
)
from switchyard.export import (
    ExportDocument,
    ExportVerifyResult,
    build_export,
    verify_export_file,
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
    "Anthropic",
    "AsyncAnthropic",
    "DetectionEngine",
    "DetectionResult",
    "Detector",
    "ExportDocument",
    "ExportVerifyResult",
    "Ledger",
    "LedgerEntry",
    "PrivacyMode",
    "RequestRecord",
    "ResponseObservation",
    "VerifyResult",
    "__version__",
    "build_export",
    "context",
    "verify_export_file",
]
