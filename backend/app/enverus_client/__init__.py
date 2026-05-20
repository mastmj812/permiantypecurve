"""Enverus client package.

`base.py` defines the abstract interface every adapter must satisfy.
`prism.py` wraps Enverus' DeveloperAPIv3 Python SDK — the SDK handles
HTTP, OAuth refresh, pagination, and 429 backoff; our wrapper only deals
in dataset names + field-name mapping.
`di_direct.py` is a fallback stub for fields Prism doesn't expose.

Tests inject a fake SDK client (any object with a `query()` method that
yields dicts) into PrismClient via the `sdk_client=` kwarg — no real
network calls in CI.
"""

from app.enverus_client.base import (
    EnverusClient,
    ProductionRecord,
    WellHeader,
)
from app.enverus_client.di_direct import DIDirectClient
from app.enverus_client.exceptions import (
    EnverusAuthError,
    EnverusClientError,
    EnverusRateLimitError,
)
from app.enverus_client.prism import PrismClient

__all__ = [
    "DIDirectClient",
    "EnverusAuthError",
    "EnverusClient",
    "EnverusClientError",
    "EnverusRateLimitError",
    "PrismClient",
    "ProductionRecord",
    "WellHeader",
]
