"""Claude Code routine: fire API trigger → webhook callback → 3-minute fallback."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.claude_routine import ClaudeRoutineJob
from app.models.user_llm_settings import UserLlmSettings
from app.services.http_ssl import httpx_verify
from app.services.wordpress_service import decrypt_api_key

logger = logging.getLogger(__name__)

ROUTINE_WAIT_MS = 3 * 60 * 1000
POLL_MS = 3000
FIRE_TIMEOUT_MS = 15_000
FIRE_HOST = "api.anthropic.com"
FIRE_BETA = "experimental-cc-routine-2026-04-01"
MAX_TEXT = 100_000
CALLBACK_PATH = "/api/webhooks/claude-routine"


@dataclass(frozen=True)
class RoutineConfig:
    fire_url: str
    token: str
    secret: str


def public_base_url() -> Optional[str]:
    raw = (getattr(settings, "api_public_url", None) or "").strip()
    if not raw:
        return None
    try:
        u = urlparse(raw)
        host = (u.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.startswith("127."):
            return None
        return f"{u.scheme}://{u.netloc}".rstrip("/")
    except Exception:
        return None


def _decrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return decrypt_api_key(value)
    except Exception:
        return None


def validate_fire_url(fire_url: str) -> str:
    """Return cleaned fire URL or raise ValueError if not Anthropic HTTPS fire endpoint."""
    cleaned = (fire_url or "").strip()
    if not cleaned:
        raise ValueError("Routine fire URL is required")
    try:
        u = urlparse(cleaned)
    except Exception as exc:
        raise ValueError("Invalid routine fire URL") from exc
    if u.scheme != "https" or (u.hostname or "").lower() != FIRE_HOST:
        raise ValueError(
            f"Routine fire URL must be https://{FIRE_HOST}/v1/claude_code/routines/…/fire"
        )
    if "/claude_code/routines/" not in (u.path or "") or not (u.path or "").rstrip("/").endswith(
        "/fire"
    ):
        raise ValueError(
            "Routine fire URL must point to a Claude Code routine …/routines/trig_…/fire endpoint"
        )
    return cleaned


def is_valid_fire_url(fire_url: Optional[str]) -> bool:
    try:
        validate_fire_url(fire_url or "")
        return True
    except ValueError:
        return False


def load_routine_config(db: Session, user_id: int) -> Optional[RoutineConfig]:
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user_id).first()
    if not row:
        return None
    fire_url = (getattr(row, "claude_routine_url", None) or "").strip()
    token = _decrypt(getattr(row, "claude_routine_token", None))
    secret = _decrypt(getattr(row, "claude_routine_callback_secret", None))
    if not fire_url or not token or not secret:
        return None
    if not is_valid_fire_url(fire_url):
        return None
    return RoutineConfig(fire_url=fire_url, token=token, secret=secret)


def is_routine_configured(db: Session, user_id: int) -> bool:
    return load_routine_config(db, user_id) is not None


def bearer_matches(header: Optional[str], secret: str) -> bool:
    given = (header or "").replace("Bearer ", "").replace("bearer ", "").strip()
    want = secret or ""
    if len(given) != len(want):
        return False
    return hmac.compare_digest(given.encode("utf-8"), want.encode("utf-8"))


async def _fire_routine(
    cfg: RoutineConfig,
    payload: dict,
) -> bool:
    try:
        async with httpx.AsyncClient(timeout=FIRE_TIMEOUT_MS / 1000.0, verify=httpx_verify()) as client:
            res = await client.post(
                cfg.fire_url,
                headers={
                    "Authorization": f"Bearer {cfg.token}",
                    "anthropic-beta": FIRE_BETA,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={"text": json.dumps(payload)},
            )
            return res.is_success
    except Exception as exc:
        logger.warning("Claude routine fire failed: %s", exc)
        return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def generate_via_claude_routine(
    user_id: int,
    prompt: str,
    *,
    system: Optional[str] = None,
) -> Optional[str]:
    """
    Fire Claude Code routine and wait up to 3 minutes for webhook text.
    Returns text on success, None to fall back to normal LLM providers.
    """
    base = public_base_url()
    if not base:
        logger.info("Claude routine skipped: API_PUBLIC_URL not publicly reachable")
        return None

    db = SessionLocal()
    try:
        cfg = load_routine_config(db, user_id)
        if not cfg:
            return None

        job_id = str(uuid.uuid4())
        wait_until = _utcnow() + timedelta(milliseconds=ROUTINE_WAIT_MS)
        full_prompt = prompt
        if system and system.strip():
            full_prompt = f"{system.strip()}\n\n{prompt}"

        job = ClaudeRoutineJob(
            id=job_id,
            user_id=user_id,
            status="waiting",
            prompt=full_prompt[:MAX_TEXT],
            wait_until=wait_until,
        )
        db.add(job)
        db.commit()

        fired = await _fire_routine(
            cfg,
            {
                "jobId": job_id,
                "prompt": full_prompt[:MAX_TEXT],
                "callbackUrl": f"{base}{CALLBACK_PATH}",
            },
        )
        if not fired:
            job.status = "failed"
            job.error_message = "Failed to fire Claude routine"
            job.completed_at = _utcnow()
            db.commit()
            return None
    finally:
        db.close()

    deadline = wait_until.timestamp()
    while datetime.now(timezone.utc).timestamp() < deadline:
        await asyncio.sleep(POLL_MS / 1000.0)
        poll = SessionLocal()
        try:
            row = poll.query(ClaudeRoutineJob).filter(ClaudeRoutineJob.id == job_id).first()
            if not row:
                return None
            if row.status == "completed" and (row.result_text or "").strip():
                return row.result_text.strip()
            if row.status in ("failed", "expired"):
                return None
        finally:
            poll.close()

    # Timed out — ignore late callbacks
    expire = SessionLocal()
    try:
        row = expire.query(ClaudeRoutineJob).filter(ClaudeRoutineJob.id == job_id).first()
        if row and row.status == "waiting":
            row.status = "expired"
            row.error_message = "Timed out waiting for Claude routine callback"
            row.completed_at = _utcnow()
            expire.commit()
    finally:
        expire.close()
    return None


def store_routine_callback(
    db: Session,
    *,
    job_id: str,
    text: str,
    authorization: Optional[str],
) -> dict:
    """Validate callback and store result. Returns response payload."""
    job = db.query(ClaudeRoutineJob).filter(ClaudeRoutineJob.id == job_id).first()
    if not job:
        return {"ok": True, "ignored": True, "reason": "unknown_job"}

    cfg = load_routine_config(db, job.user_id)
    if not cfg or not bearer_matches(authorization, cfg.secret):
        return {"ok": False, "error": "unauthorized", "status": 401}

    if job.status != "waiting" or job.result_text:
        return {"ok": True, "ignored": True, "reason": "already_done"}

    cleaned = (text or "").strip()[:MAX_TEXT]
    if not cleaned:
        return {"ok": False, "error": "text required", "status": 400}

    job.result_text = cleaned
    job.status = "completed"
    job.completed_at = _utcnow()
    db.commit()
    return {"ok": True, "stored": True}
