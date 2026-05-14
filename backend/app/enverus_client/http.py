"""Shared HTTP transport for Enverus adapters.

Built on httpx so tests can swap in `httpx.MockTransport` for full
hermetic control. Centralizes:
  * auth header injection (key read at request time → trivial to rotate)
  * retry with jitter on 429 / 5xx
  * `Retry-After` honoring (Enverus surfaces it on 429)
  * structured logging of failures (no payload bodies — they can be large
    and may contain PII for non-public well data)
"""

from __future__ import annotations

import random
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.logging import get_logger
from app.enverus_client.exceptions import (
    EnverusAuthError,
    EnverusClientError,
    EnverusRateLimitError,
)

log = get_logger("enverus.http")

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 5


class _RetryableStatus(Exception):
    """Sentinel raised on transient HTTP errors so tenacity can catch + retry."""


class EnverusHTTPClient:
    """Thin wrapper over `httpx.Client` with Enverus-aware retry semantics.

    Inject a custom `transport` (e.g. `httpx.MockTransport`) for testing.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        auth_header: str = "X-API-Key",
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_wait_initial: float = 1.0,
        retry_wait_max: float = 30.0,
        retry_wait_jitter: float = 2.0,
        transport: httpx.BaseTransport | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._auth_header = auth_header
        self._extra_headers = extra_headers or {}
        self._max_retries = max_retries
        self._retry_wait_initial = retry_wait_initial
        self._retry_wait_max = retry_wait_max
        self._retry_wait_jitter = retry_wait_jitter
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EnverusHTTPClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        # Re-read the api key on every request so callers can rotate it
        # without rebuilding the client (a hook for refresh logic when /if
        # Enverus moves to short-lived tokens).
        h = {"Accept": "application/json", **self._extra_headers}
        if self._api_key:
            h[self._auth_header] = self._api_key
        return h

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET with retry. Returns the parsed JSON body."""

        def _log_retry(rs: RetryCallState) -> None:
            log.warning(
                "enverus_retry",
                attempt=rs.attempt_number,
                outcome=str(rs.outcome.exception()) if rs.outcome else None,
                path=path,
            )

        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(
                initial=self._retry_wait_initial,
                max=self._retry_wait_max,
                jitter=self._retry_wait_jitter,
            ),
            retry=retry_if_exception_type((_RetryableStatus, httpx.TransportError)),
            before_sleep=_log_retry,
        )
        def _do() -> dict[str, Any]:
            resp = self._client.get(path, params=params, headers=self._headers())
            return self._handle(resp, path=path)

        return _do()

    def _handle(self, resp: httpx.Response, *, path: str) -> dict[str, Any]:
        if 200 <= resp.status_code < 300:
            return resp.json()  # type: ignore[no-any-return]
        if resp.status_code in (401, 403):
            raise EnverusAuthError(f"{resp.status_code} from {path}: {resp.text[:200]}")
        if resp.status_code == 429:
            retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
            log.warning("enverus_429", path=path, retry_after=retry_after)
            # Jitter the wait so two pulls retrying in lockstep don't synchronize.
            if retry_after is not None:
                # tenacity controls the delay; signal to it via a TransportError
                # would be cleaner, but we want the Retry-After value to flow
                # to the caller too.
                raise EnverusRateLimitError("rate limited", retry_after_seconds=retry_after)
            raise _RetryableStatus(f"429 from {path}")
        if 500 <= resp.status_code < 600:
            raise _RetryableStatus(f"{resp.status_code} from {path}: {resp.text[:200]}")
        raise EnverusClientError(f"{resp.status_code} from {path}: {resp.text[:200]}")

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value) + random.uniform(0, 1.0)
        except ValueError:
            # HTTP-date form — could parse, but Enverus uses seconds.
            return None
