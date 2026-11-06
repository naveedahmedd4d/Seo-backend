"""Live model catalogs, sourced from each vendor instead of a hardcoded list.

Provider catalogs change without notice: four providers broke between two
deployments, every failure a retired model rather than a bad key. A list
written by hand goes stale silently and the user only finds out when
generation 404s. So the allowlist is checked against what the vendor itself
reports, and anything the vendor no longer lists is marked unavailable.

Reachability is a three-state answer, and the difference matters:
  True  -> the vendor lists this id right now
  False -> the vendor answered and did NOT list it (retired)
  None  -> we could not ask (no key, network error, rate limit)
`None` must never render as "dead": an unreachable vendor is not evidence
about a model.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List, Optional, Set

import httpx

from app.services.http_ssl import httpx_verify

logger = logging.getLogger(__name__)

# Vendor catalogs change on the order of weeks; re-asking every few minutes is
# enough to catch a retirement without adding a vendor round-trip per page load.
CACHE_TTL_SECONDS = 600
_FETCH_TIMEOUT = 10.0

CATALOG_URLS: Dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "cohere": "https://api.cohere.com/v1/models",
    "nvidia": "https://integrate.api.nvidia.com/v1/models",
    # cloudflare is per-account; built in _catalog_request
}

# Catalogs that answer without a key. The rest need the user's own key, so
# their models stay "unknown" until one is saved.
PUBLIC_CATALOGS = {"openrouter", "nvidia"}

_cache: Dict[str, tuple[float, List[str]]] = {}


def clear_cache() -> None:
    _cache.clear()


def cached(key: str, producer: Callable[[], List[str]], ttl: int = CACHE_TTL_SECONDS) -> List[str]:
    """Return a cached list, refreshing it when older than `ttl` seconds."""
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and ttl > 0 and (now - hit[0]) < ttl:
        return hit[1]
    value = producer()
    _cache[key] = (now, value)
    return value


def _strip_gemini_prefix(name: str) -> str:
    return name[len("models/"):] if name.startswith("models/") else name


def parse_models(provider: str, body: object) -> List[str]:
    """Pull generation-capable model ids out of a vendor's catalog response.

    Shapes differ per vendor, and every vendor also lists models that cannot
    generate text (embeddings, rerank, safety, parsing). Offering those as a
    blog model would fail at generation time, so they are filtered here.
    Unrecognised shapes yield [] rather than raising: a catalog we cannot
    parse must read as "unknown", never as "everything is dead".
    """
    if not isinstance(body, dict):
        return []

    if provider == "gemini":
        out = []
        for m in body.get("models") or []:
            if not isinstance(m, dict):
                continue
            methods = m.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            name = m.get("name") or ""
            if name:
                out.append(_strip_gemini_prefix(name))
        return out

    if provider == "cohere":
        out = []
        for m in body.get("models") or []:
            if not isinstance(m, dict):
                continue
            endpoints = m.get("endpoints") or []
            if endpoints and "chat" not in endpoints:
                continue
            if m.get("name"):
                out.append(m["name"])
        return out

    if provider == "cloudflare":
        out = []
        for m in body.get("result") or []:
            if not isinstance(m, dict):
                continue
            task = (m.get("task") or {}).get("name") or ""
            if task and task.lower() != "text generation":
                continue
            if m.get("name"):
                out.append(m["name"])
        return out

    # openai-shaped: groq, mistral, nvidia, openrouter
    out = []
    for m in body.get("data") or []:
        if isinstance(m, dict) and m.get("id"):
            out.append(m["id"])
    return out


def _catalog_request(
    provider: str, api_key: Optional[str], account_id: Optional[str]
) -> tuple[str, dict]:
    headers: Dict[str, str] = {}
    if provider == "cloudflare":
        if not account_id:
            raise ValueError("cloudflare catalog needs an account id")
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            "/ai/models/search?task=Text+Generation"
        )
        headers["Authorization"] = f"Bearer {api_key}"
        return url, headers

    url = CATALOG_URLS[provider]
    if provider == "gemini":
        # Gemini takes the key as a query param on this endpoint.
        return f"{url}?key={api_key}&pageSize=200", headers
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return url, headers


def fetch_live_ids(
    provider: str, api_key: Optional[str] = None, account_id: Optional[str] = None
) -> Set[str]:
    """Ask the vendor which models it currently serves.

    Raises on any failure so the caller can tell "vendor says no" apart from
    "we could not ask" -- the distinction the three-state availability rests on.
    """
    if provider not in CATALOG_URLS and provider != "cloudflare":
        raise ValueError(f"no catalog endpoint for {provider}")
    if not api_key and provider not in PUBLIC_CATALOGS:
        raise ValueError(f"{provider} catalog requires an api key")

    url, headers = _catalog_request(provider, api_key, account_id)

    def _go() -> List[str]:
        with httpx.Client(timeout=_FETCH_TIMEOUT, verify=httpx_verify()) as client:
            response = client.get(url, headers=headers)
        response.raise_for_status()
        return parse_models(provider, response.json())

    # Key the cache per account for cloudflare, whose catalog is account-scoped.
    cache_key = f"{provider}:{account_id}" if provider == "cloudflare" else provider
    return set(cached(cache_key, _go))


def availability_map(
    providers: Dict[str, tuple[Optional[str], Optional[str]]],
) -> Dict[str, Optional[Set[str]]]:
    """Live id sets per provider; None where the vendor could not be reached."""
    out: Dict[str, Optional[Set[str]]] = {}
    for provider, (api_key, account_id) in providers.items():
        try:
            out[provider] = fetch_live_ids(provider, api_key, account_id)
        except Exception as exc:  # unreachable -> unknown, never "dead"
            logger.info("llm catalog unreachable for %s: %s", provider, exc)
            out[provider] = None
    return out
