class EnverusClientError(Exception):
    """Base class for all client errors."""


class EnverusAuthError(EnverusClientError):
    """401/403 from the upstream — likely expired or missing API key."""


class EnverusRateLimitError(EnverusClientError):
    """429 from the upstream. Retry after the Retry-After hint."""

    def __init__(self, message: str, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
