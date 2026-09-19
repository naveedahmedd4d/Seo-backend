import asyncio
import logging
import ssl
from typing import Awaitable, Callable, TypeVar, Union

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def httpx_verify() -> Union[bool, ssl.SSLContext]:
    """TLS verification for httpx — prefers OS trust store (Windows/corporate CA)."""
    if not settings.groq_ssl_verify:
        return False
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return True


_RETRYABLE_ERRORS = (ssl.SSLError, httpx.ConnectError, httpx.ReadTimeout, httpx.TransportError)


async def with_ssl_retry(
    request_fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    """Retry a request on transient SSL/connection errors.

    `request_fn` should build a fresh client (fresh SSLContext) on every call —
    don't pass a closure over an already-open client. Corporate AV/endpoint
    protection can occasionally fail a single TLS handshake or OS cert-store
    enumeration without it being a persistent trust problem; a fresh attempt
    resolves this in practice far more often than not.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await request_fn()
        except _RETRYABLE_ERRORS as exc:
            last_exc = exc
            if attempt < attempts - 1:
                logger.warning(
                    "Transient network/SSL error (attempt %d/%d): %s",
                    attempt + 1,
                    attempts,
                    exc,
                )
                await asyncio.sleep(base_delay * (attempt + 1))
                continue
            raise
    raise last_exc  # pragma: no cover — loop always returns or raises
