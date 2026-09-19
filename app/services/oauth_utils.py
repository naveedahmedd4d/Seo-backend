"""Shared helpers for sanitizing OAuth callback errors before they reach the browser."""
import re

_SECRET_QUERY_PATTERN = re.compile(
    r"(client_secret|access_token|fb_exchange_token|code|refresh_token|id_token)=[^&\s\"']+",
    re.IGNORECASE,
)


def sanitize_oauth_error(message: str | None) -> str:
    """Strip anything that could carry a secret before it reaches the browser.

    OAuth callback errors land in a redirect query string / postMessage payload,
    browser history, and a toast, so a raw exception string (which may embed a
    failed request URL containing client_secret / the OAuth code / a token) must
    never be forwarded verbatim.
    """
    if not message:
        return "connection_failed"
    cleaned = _SECRET_QUERY_PATTERN.sub(r"\1=REDACTED", message)
    cleaned = re.sub(r"https?://\S+", "[url]", cleaned)
    return cleaned.strip()[:300] or "connection_failed"
