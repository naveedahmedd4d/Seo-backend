"""Claude LLM relay: POST /api/v1/run then poll /api/v1/jobs/:id until done."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy.orm import Session

from app.models.user_llm_settings import UserLlmSettings
from app.services.http_ssl import httpx_verify
from app.services.wordpress_service import decrypt_api_key

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://claude-llm.vercel.app"
DEFAULT_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 2_000
MAX_TEXT = 100_000
RUN_PATH = "/api/v1/run"
JOB_PATH = "/api/v1/jobs"
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TERMINAL_OK = {"DONE", "COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED"}
_TERMINAL_FAIL = {"FAILED", "ERROR", "CANCELLED", "CANCELED", "TIMEOUT", "EXPIRED"}


class ClaudeLlmError(Exception):
    """Claude LLM relay request failed."""


@dataclass(frozen=True)
class ClaudeLlmConfig:
    api_key: str
    base_url: str
    webhook_secret: Optional[str]
    timeout_ms: int


def _decrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return decrypt_api_key(value)
    except Exception:
        return None


def _ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _hostname_allowed(hostname: str) -> bool:
    """Reject obvious local/private targets without doing DNS (save-time safe)."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        return _ip_is_public(ipaddress.ip_address(host))
    except ValueError:
        return True


def assert_public_base_url(base_url: str) -> None:
    """Resolve the host and reject private/link-local answers (request-time)."""
    host = (urlparse(base_url).hostname or "").strip().lower()
    if not host or not _hostname_allowed(host):
        raise ClaudeLlmError("Claude LLM base URL host is not allowed")
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ClaudeLlmError("Claude LLM base URL host could not be resolved") from exc
    if not infos:
        raise ClaudeLlmError("Claude LLM base URL host could not be resolved")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            raise ClaudeLlmError("Claude LLM base URL resolved to an invalid address") from exc
        if not _ip_is_public(ip):
            raise ClaudeLlmError("Claude LLM base URL resolved to a private address")


def normalize_base_url(raw: Optional[str]) -> str:
    cleaned = (raw or "").strip().rstrip("/")
    if not cleaned:
        return DEFAULT_BASE_URL
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(
            "Claude LLM base URL must be an https origin, e.g. https://claude-llm.vercel.app"
        )
    host = (parsed.hostname or "").strip().lower()
    if not host or not _hostname_allowed(host):
        raise ValueError("Claude LLM base URL host must be a public https origin")
    # Origin only — strip any path the user may have pasted.
    return f"https://{parsed.netloc.lower()}"


def normalize_timeout_ms(raw: Optional[int]) -> int:
    if raw is None:
        return DEFAULT_TIMEOUT_MS
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Claude LLM job timeout must be an integer number of milliseconds") from exc
    if value < 5_000 or value > 600_000:
        raise ValueError("Claude LLM job timeout must be between 5000 and 600000 ms")
    return value


def load_claude_llm_config(db: Session, user_id: int) -> Optional[ClaudeLlmConfig]:
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user_id).first()
    if not row:
        return None
    api_key = _decrypt(getattr(row, "claude_llm_api_key", None))
    if not api_key:
        return None
    try:
        base_url = normalize_base_url(getattr(row, "claude_llm_base_url", None))
        timeout_ms = normalize_timeout_ms(getattr(row, "claude_llm_timeout_ms", None))
    except ValueError:
        return None
    webhook_secret = _decrypt(getattr(row, "claude_llm_webhook_secret", None))
    return ClaudeLlmConfig(
        api_key=api_key,
        base_url=base_url,
        webhook_secret=webhook_secret,
        timeout_ms=timeout_ms,
    )


def is_claude_llm_configured(db: Session, user_id: int) -> bool:
    return load_claude_llm_config(db, user_id) is not None


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _extract_text(result: Any) -> Optional[str]:
    """Pull generated text from the relay `result` field only."""
    if result is None:
        return None
    if isinstance(result, str):
        text = result.strip()
        return text or None
    if isinstance(result, dict):
        for key in ("text", "content", "output", "greeting", "answer", "body"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = result.get("data")
        if nested is not None and nested is not result:
            return _extract_text(nested)
    return None


def _job_done(payload: dict[str, Any]) -> bool:
    if payload.get("error"):
        return True
    status = str(payload.get("status") or "").upper()
    if status in _TERMINAL_OK | _TERMINAL_FAIL:
        return True
    # Sync-style responses include result with no status / a terminal status.
    # Do not stop early on RUNNING/QUEUED even if a partial result is present.
    if payload.get("result") is not None and status not in {
        "RUNNING",
        "QUEUED",
        "PENDING",
        "PROCESSING",
        "IN_PROGRESS",
    }:
        return True
    return False


def _job_failed(payload: dict[str, Any]) -> Optional[str]:
    err = payload.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        msg = err.get("message") or err.get("error") or err.get("detail")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    status = str(payload.get("status") or "").upper()
    if status in _TERMINAL_FAIL:
        return f"Claude LLM job {status.lower()}"
    return None


def _safe_job_id(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    job_id = raw.strip()
    if not _JOB_ID_RE.fullmatch(job_id):
        return None
    return job_id


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    deadline: float,
) -> dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(3):
        if time.monotonic() >= deadline:
            break
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            last_error = exc
            await asyncio.sleep(min(1.5 * (attempt + 1), max(0.0, deadline - time.monotonic())))
            continue
        if response.status_code in {401, 403, 404}:
            raise ClaudeLlmError(f"Claude LLM job poll failed ({response.status_code})")
        if response.status_code >= 500:
            last_error = ClaudeLlmError(f"Claude LLM job poll failed ({response.status_code})")
            await asyncio.sleep(min(1.5 * (attempt + 1), max(0.0, deadline - time.monotonic())))
            continue
        if response.status_code >= 400:
            raise ClaudeLlmError(f"Claude LLM job poll failed ({response.status_code})")
        data = response.json()
        if not isinstance(data, dict):
            raise ClaudeLlmError("Claude LLM job response was not an object")
        return data
    if last_error:
        raise ClaudeLlmError(f"Claude LLM job poll failed: {last_error}")
    raise ClaudeLlmError("Claude LLM job poll failed")


async def run_claude_llm_job(
    cfg: ClaudeLlmConfig,
    text: str,
    *,
    wait: bool = True,
) -> dict[str, Any]:
    """Submit a job to the Claude LLM relay. Optionally poll until complete."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ClaudeLlmError("text is required")
    cleaned = cleaned[:MAX_TEXT]

    assert_public_base_url(cfg.base_url)

    run_url = f"{cfg.base_url}{RUN_PATH}"
    headers = _auth_headers(cfg.api_key)
    timeout = httpx.Timeout(max(30.0, cfg.timeout_ms / 1000.0 + 5.0))
    deadline = time.monotonic() + (cfg.timeout_ms / 1000.0)

    async with httpx.AsyncClient(timeout=timeout, verify=httpx_verify()) as client:
        response = await client.post(run_url, headers=headers, json={"text": cleaned})
        if response.status_code >= 400:
            raise ClaudeLlmError(f"Claude LLM run failed ({response.status_code})")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ClaudeLlmError("Claude LLM run response was not an object")

        if not wait or _job_done(payload):
            return payload

        job_id = _safe_job_id(payload.get("job_id") or payload.get("id"))
        if not job_id:
            if payload.get("result") is not None:
                return payload
            raise ClaudeLlmError("Claude LLM run response missing a valid job_id")

        job_url = f"{cfg.base_url}{JOB_PATH}/{quote(job_id, safe='')}"
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL_MS / 1000.0)
            payload = await _get_json(client, job_url, headers, deadline=deadline)
            if _job_done(payload):
                return payload

        raise ClaudeLlmError(f"Claude LLM job timed out after {cfg.timeout_ms} ms")


async def generate_via_claude_llm(
    user_id: int,
    prompt: str,
    *,
    system: Optional[str] = None,
) -> Optional[str]:
    """Run generation through the Claude LLM relay. Returns text or None to fall back."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        cfg = load_claude_llm_config(db, user_id)
    finally:
        db.close()
    if not cfg:
        return None

    full_prompt = prompt
    if system and system.strip():
        full_prompt = f"{system.strip()}\n\n{prompt}"

    try:
        payload = await run_claude_llm_job(cfg, full_prompt, wait=True)
    except ClaudeLlmError as exc:
        logger.warning("Claude LLM generation failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Claude LLM generation error: %s", exc)
        return None

    failed = _job_failed(payload)
    if failed:
        logger.warning("Claude LLM job error: %s", failed)
        return None

    return _extract_text(payload.get("result"))


async def test_claude_llm_connection(
    *,
    api_key: str,
    base_url: Optional[str] = None,
    timeout_ms: Optional[int] = None,
) -> tuple[bool, str]:
    """Send a short hello job to confirm the key works."""
    try:
        cfg = ClaudeLlmConfig(
            api_key=api_key.strip(),
            base_url=normalize_base_url(base_url),
            webhook_secret=None,
            timeout_ms=normalize_timeout_ms(timeout_ms),
        )
    except ValueError as exc:
        return False, str(exc)

    try:
        payload = await run_claude_llm_job(cfg, "hello", wait=True)
    except ClaudeLlmError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not reach Claude LLM: {exc}"

    failed = _job_failed(payload)
    if failed:
        return False, failed

    text = _extract_text(payload.get("result"))
    if text:
        preview = text if len(text) <= 120 else f"{text[:117]}…"
        return True, f"Connected — {preview}"
    if payload.get("result") is not None:
        job_id = payload.get("job_id") or payload.get("id") or "unknown"
        return True, f"Connected — job {job_id} completed"
    return False, "Claude LLM job completed without a result"
