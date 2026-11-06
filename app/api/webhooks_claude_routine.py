"""Webhook for Claude Code routine callbacks."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.claude_routine_service import store_routine_callback

router = APIRouter()


class ClaudeRoutineCallbackBody(BaseModel):
    jobId: Optional[str] = Field(default=None, alias="jobId")
    job_id: Optional[str] = None
    text: Optional[str] = None

    class Config:
        populate_by_name = True


@router.post("/claude-routine")
async def claude_routine_webhook(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """
    Callback for Claude Code routines.
    Auth: Authorization: Bearer <callback secret>
    Body: { "jobId": "<uuid>", "text": "<result>" }
    """
    try:
        raw: Any = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

    job_id = ""
    text = ""
    if isinstance(raw, dict):
        job_id = str(raw.get("jobId") or raw.get("job_id") or "").strip()
        text = str(raw.get("text") or "").strip()

    if not job_id or not text:
        return JSONResponse(
            {"ok": False, "error": "jobId and text required"},
            status_code=400,
        )

    result = store_routine_callback(
        db,
        job_id=job_id,
        text=text,
        authorization=authorization,
    )
    status = int(result.pop("status", 200))
    return JSONResponse(result, status_code=status)
