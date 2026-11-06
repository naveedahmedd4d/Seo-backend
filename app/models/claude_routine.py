"""Claude Code routine job tracking for fire → webhook → fallback flow."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class ClaudeRoutineJob(Base):
    __tablename__ = "claude_routine_jobs"

    id = Column(String(64), primary_key=True)  # UUID job id
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(24), nullable=False, server_default="waiting")  # waiting|completed|failed|expired
    prompt = Column(Text, nullable=True)
    result_text = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    wait_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
