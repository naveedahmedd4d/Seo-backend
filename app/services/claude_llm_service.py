"""Claude LLM relay: POST /api/v1/run/:endpointId then poll /api/v1/jobs/:id."""

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

DEFAULT_ORIGIN = "https://claude-llm.vercel.app"
DEFAULT_TIMEOUT_MS = 120_000
POLL_INTERVAL_MS = 2_000
MAX_TEXT = 100_000
RUN_PATH = "/api/v1/run"
JOB_PATH = "/api/v1/jobs"
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ENDPOINT_ID_RE = re.compile(
    r"^/api/v1/run/([A-Za-z0-9][A-Za-z0-9_-]{0,127})$"
)
_TERMINAL_OK = {"DONE", "COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED"}
_TERMINAL_FAIL = {"FAILED", "ERROR", "CANCELLED", "CANCELED", "TIMEOUT", "EXPIRED"}


class ClaudeLlmError(Exception):
    """Claude LLM relay request failed."""


@dataclass(frozen=True)
class ClaudeLlmConfig:
    api_key: str
    """Full POST URL, e.g. https://host/api/v1/run/<endpoint-id>."""
    run_url: str
    """Origin used to build /api/v1/jobs/:id."""
    origin: str
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


def assert_public_origin(origin: str) -> None:
    """Resolve the host and reject private/link-local answers (request-time)."""
    host = (urlparse(origin).hostname or "").strip().lower()
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


def normalize_run_url(raw: Optional[str]) -> tuple[str, str]:
    """
    Return (run_url, origin).

    Accepts:
    - https://host
    - https://host/api/v1/run
    - https://host/api/v1/run/<endpoint-id>  (preferred)
    """
    cleaned = (raw or "").strip().rstrip("/")
    if not cleaned:
        # Origin alone cannot call the relay without an endpoint id.
        raise ValueError(
            "Claude LLM URL must include the endpoint id, e.g. "
            "https://claude-llm.vercel.app/api/v1/run/<endpoint-id>"
        )
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(
            "Claude LLM URL must be https, e.g. "
            "https://claude-llm.vercel.app/api/v1/run/<endpoint-id>"
        )
    host = (parsed.hostname or "").strip().lower()
    if not host or not _hostname_allowed(host):
        raise ValueError("Claude LLM URL host must be a public https origin")

    origin = f"https://{parsed.netloc.lower()}"
    path = (parsed.path or "").rstrip("/") or ""

    if not path:
        raise ValueError(
            "Paste the full run URL including /api/v1/run/<endpoint-id> "
            "(not just the site origin)."
        )

    if path == RUN_PATH:
        raise ValueError(
            "Paste the full run URL including your endpoint id, e.g. "
            f"{origin}{RUN_PATH}/<endpoint-id>"
        )

    match = _ENDPOINT_ID_RE.match(path)
    if not match:
        raise ValueError(
            "Claude LLM URL path must be /api/v1/run/<endpoint-id>"
        )

    endpoint_id = match.group(1)
    run_url = f"{origin}{RUN_PATH}/{endpoint_id}"
    return run_url, origin


# Back-compat name used by settings upsert / tests.
def normalize_base_url(raw: Optional[str]) -> str:
    """Normalize and return the stored run URL (keeps endpoint id)."""
    if not (raw or "").strip():
        return DEFAULT_ORIGIN
    run_url, _origin = normalize_run_url(raw)
    return run_url


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


def _platform_env_config() -> Optional[ClaudeLlmConfig]:
    from app.config import settings

    api_key = getattr(settings, "claude_llm_api_key", None)
    run_raw = getattr(settings, "claude_llm_url", None)
    if not api_key or not str(api_key).strip() or not run_raw or not str(run_raw).strip():
        return None
    try:
        run_url, origin = normalize_run_url(str(run_raw))
        timeout_ms = normalize_timeout_ms(getattr(settings, "claude_llm_timeout_ms", None))
    except ValueError:
        return None
    secret = getattr(settings, "claude_llm_webhook_secret", None)
    return ClaudeLlmConfig(
        api_key=str(api_key).strip(),
        run_url=run_url,
        origin=origin,
        webhook_secret=str(secret).strip() if secret else None,
        timeout_ms=timeout_ms,
    )


def load_claude_llm_config(db: Session, user_id: int) -> Optional[ClaudeLlmConfig]:
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user_id).first()
    api_key = _decrypt(getattr(row, "claude_llm_api_key", None)) if row else None
    raw_url = getattr(row, "claude_llm_base_url", None) if row else None
    webhook_secret = (
        _decrypt(getattr(row, "claude_llm_webhook_secret", None)) if row else None
    )
    timeout_raw = getattr(row, "claude_llm_timeout_ms", None) if row else None

    if api_key and raw_url and str(raw_url).strip():
        try:
            run_url, origin = normalize_run_url(str(raw_url))
            timeout_ms = normalize_timeout_ms(timeout_raw)
        except ValueError:
            pass
        else:
            return ClaudeLlmConfig(
                api_key=api_key,
                run_url=run_url,
                origin=origin,
                webhook_secret=webhook_secret,
                timeout_ms=timeout_ms,
            )

    # Fall back to server env (ClaudeLLM_URL / ClaudeLLM_API_KEY / …).
    return _platform_env_config()


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
    if isinstance(result, (list, tuple)):
        parts = [_extract_text(item) for item in result]
        joined = "\n".join(p for p in parts if p)
        return joined or None
    if isinstance(result, dict):
        for key in (
            "text",
            "content",
            "output",
            "greeting",
            "answer",
            "body",
            "message",
            "response",
            "result",
        ):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (dict, list, tuple)):
                nested_text = _extract_text(value)
                if nested_text:
                    return nested_text
        nested = result.get("data")
        if nested is not None and nested is not result:
            return _extract_text(nested)
        # Some relays return the full JSON object as the answer (e.g. social bundle).
        try:
            import json

            dumped = json.dumps(result, ensure_ascii=False)
            if dumped and dumped not in ("{}", "[]", "null"):
                return dumped
        except Exception:  # noqa: BLE001
            pass
    return None


def _job_done(payload: dict[str, Any]) -> bool:
    if payload.get("error"):
        return True
    status = str(payload.get("status") or "").upper()
    if status in _TERMINAL_OK | _TERMINAL_FAIL:
        return True
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
    instructions: Optional[str] = None,
) -> dict[str, Any]:
    """Submit a job to the Claude LLM relay. Optionally poll until complete."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ClaudeLlmError("context is required")
    cleaned = cleaned[:MAX_TEXT]

    assert_public_origin(cfg.origin)

    headers = _auth_headers(cfg.api_key)
    timeout = httpx.Timeout(max(30.0, cfg.timeout_ms / 1000.0 + 5.0))
    deadline = time.monotonic() + (cfg.timeout_ms / 1000.0)

    # Relay requires `context` (string or object). `text` alone returns 400.
    body: dict[str, Any] = {"context": cleaned}
    if instructions and instructions.strip():
        body["instructions"] = instructions.strip()[:MAX_TEXT]

    async with httpx.AsyncClient(timeout=timeout, verify=httpx_verify()) as client:
        response = await client.post(cfg.run_url, headers=headers, json=body)
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = str(
                        body.get("error")
                        or body.get("message")
                        or body.get("detail")
                        or ""
                    ).strip()
            except Exception:  # noqa: BLE001
                detail = (response.text or "").strip()[:120]
            suffix = f": {detail}" if detail else ""
            raise ClaudeLlmError(f"Claude LLM run failed ({response.status_code}){suffix}")
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

        job_url = f"{cfg.origin}{JOB_PATH}/{quote(job_id, safe='')}"
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

    try:
        payload = await run_claude_llm_job(
            cfg,
            prompt,
            wait=True,
            instructions=(system.strip() if system and system.strip() else None),
        )
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
        run_url, origin = normalize_run_url(base_url)
        cfg = ClaudeLlmConfig(
            api_key=api_key.strip(),
            run_url=run_url,
            origin=origin,
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
