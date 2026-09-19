import asyncio
import logging
import ssl
from typing import Awaitable, Callable, TypeVar, Union

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def httpx_verify() -> Union[bool, ssl.SSLContext]:
    """Return an SSL context that trusts both public and OS-installed CAs.

    Certifi is the most predictable source for public roots, while the operating
    system store is where Windows and managed machines keep locally installed
    proxy/corporate issuers. Loading both into a regular ``SSLContext`` avoids
    certificate-chain failures without disabling verification.
    """
    if not settings.groq_ssl_verify:
        return False

    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    # Passing ``cafile`` replaces, rather than augments, Python's defaults. Add
    # the platform defaults explicitly so locally installed root CAs (common
    # with Windows antivirus/VPN/TLS inspection) remain trusted.
    try:
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    except Exception as exc:  # The certifi roots above are still usable.
        logger.warning("Could not load OS certificate store: %s", exc)

    # Python 3.13 enables OpenSSL's X509 strict mode in
    # ``create_default_context``. Some otherwise valid OS-managed/corporate
    # issuer certificates predate the requirement that Basic Constraints be
    # marked critical, which makes strict mode reject the chain. Keep normal
    # certificate and hostname verification, but allow those legacy issuers.
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict_flag:
        context.verify_flags &= ~strict_flag

    return context


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
