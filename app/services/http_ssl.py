import ssl
from typing import Union

from app.config import settings


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
