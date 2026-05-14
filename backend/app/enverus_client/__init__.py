"""Enverus client package.

`base.py` defines the abstract interface every adapter must satisfy.
`prism.py` is the primary REST/JSON adapter (Prism API).
`di_direct.py` is a fallback for fields Prism doesn't expose (DirectAccess).

Tests inject a mock `httpx.MockTransport` into the Prism adapter so we never
hit the real Enverus endpoints in CI.
"""

from app.enverus_client.base import (
    EnverusClient,
    DirectionalSurvey,
    ProductionRecord,
    SurveyStation,
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
    "DirectionalSurvey",
    "EnverusAuthError",
    "EnverusClient",
    "EnverusClientError",
    "EnverusRateLimitError",
    "PrismClient",
    "ProductionRecord",
    "SurveyStation",
    "WellHeader",
]
