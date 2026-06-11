"""Switchyard — detect silent Claude model fallbacks and log them locally.

Public API (grows session by session, see SESSIONS.md):

- :data:`__version__`
- :class:`switchyard.ledger.Ledger` and friends (hash-chained local ledger)

The wrapped ``Anthropic`` client and ``audit`` entry points arrive with the
capture-surface and reporting sessions.
"""

from switchyard.ledger import (
    Ledger,
    LedgerEntry,
    PrivacyMode,
    RequestRecord,
    VerifyResult,
)

__version__ = "0.1.0"

__all__ = [
    "Ledger",
    "LedgerEntry",
    "PrivacyMode",
    "RequestRecord",
    "VerifyResult",
    "__version__",
]
