"""Post to a Slack channel through an incoming webhook.

The webhook URL is the secret (it encodes workspace + channel); it lives
in the backend env only and is never returned to the browser.
"""

from __future__ import annotations

import httpx

_TIMEOUT_S = 10.0


class SlackPostError(RuntimeError):
    pass


def post_webhook(url: str, text: str) -> None:
    """Raise SlackPostError unless Slack answers 200 ``ok``."""
    try:
        r = httpx.post(url, json={"text": text}, timeout=_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise SlackPostError(f"slack webhook unreachable: {exc.__class__.__name__}") from exc
    if r.status_code != 200 or r.text.strip() != "ok":
        # Slack's error bodies are short codes (invalid_payload,
        # no_service, channel_is_archived, ...) — safe to surface.
        raise SlackPostError(f"slack webhook refused ({r.status_code}): {r.text[:200]}")
