from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class AutomationBot(Base):
    __tablename__ = "automation_bots"
    __table_args__ = (
        Index("ix_automation_bots_user_status", "user_id", "status"),
        Index("ix_automation_bots_due", "status", "next_run_at"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    wordpress_site_id = Column(
        Integer, ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, server_default="draft")
    workflow_definition = Column(JSON, nullable=False)
    settings = Column(JSON, nullable=False, default=dict)
    schedule_config = Column(JSON, nullable=False, default=dict)
    timezone = Column(String(64), nullable=False, server_default="UTC")
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    website = relationship("WordPressSite")
    executions = relationship(
        "AutomationExecution", back_populates="bot", cascade="all, delete-orphan"
    )


class AutomationExecution(Base):
    __tablename__ = "automation_executions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_automation_execution_user_key"),
        Index("ix_automation_executions_bot_created", "bot_id", "created_at"),
        Index("ix_automation_executions_user_status", "user_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    bot_id = Column(
        Integer, ForeignKey("automation_bots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    wordpress_site_id = Column(
        Integer, ForeignKey("wordpress_sites.id", ondelete="CASCADE"), nullable=True
    )
    trigger_type = Column(String(24), nullable=False, server_default="manual")
    status = Column(String(32), nullable=False, server_default="queued")
    graph_snapshot = Column(JSON, nullable=False)
    settings_snapshot = Column(JSON, nullable=False, default=dict)
    current_node_id = Column(String(80), nullable=True)
    start_node_id = Column(String(80), nullable=True)
    resumed_from_execution_id = Column(
        Integer, ForeignKey("automation_executions.id", ondelete="SET NULL"), nullable=True
    )
    error_summary = Column(Text, nullable=True)
    final_output = Column(JSON, nullable=True)
    idempotency_key = Column(String(160), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    bot = relationship("AutomationBot", back_populates="executions")
    node_executions = relationship(
        "AutomationNodeExecution", back_populates="execution", cascade="all, delete-orphan"
    )
    events = relationship(
        "AutomationEvent", back_populates="execution", cascade="all, delete-orphan"
    )


class AutomationNodeExecution(Base):
    __tablename__ = "automation_node_executions"
    __table_args__ = (
        UniqueConstraint("execution_id", "node_id", name="uq_automation_node_execution"),
        Index("ix_automation_node_executions_execution_position", "execution_id", "position"),
    )

    id = Column(Integer, primary_key=True)
    execution_id = Column(
        Integer, ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(String(80), nullable=False)
    node_type = Column(String(48), nullable=False)
    position = Column(Integer, nullable=False, server_default="0")
    attempt = Column(Integer, nullable=False, server_default="0")
    status = Column(String(32), nullable=False, server_default="pending")
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    usage_data = Column(JSON, nullable=True)
    error_data = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    execution = relationship("AutomationExecution", back_populates="node_executions")


class AutomationEvent(Base):
    __tablename__ = "automation_events"
    __table_args__ = (Index("ix_automation_events_execution_created", "execution_id", "created_at"),)

    id = Column(Integer, primary_key=True)
    execution_id = Column(
        Integer, ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=False
    )
    node_execution_id = Column(
        Integer, ForeignKey("automation_node_executions.id", ondelete="CASCADE"), nullable=True
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    level = Column(String(16), nullable=False, server_default="info")
    event_type = Column(String(48), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    execution = relationship("AutomationExecution", back_populates="events")


class AutomationApproval(Base):
    __tablename__ = "automation_approvals"
    __table_args__ = (
        UniqueConstraint("execution_id", "node_id", name="uq_automation_approval_node"),
    )

    id = Column(Integer, primary_key=True)
    execution_id = Column(
        Integer, ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(String(80), nullable=False)
    status = Column(String(24), nullable=False, server_default="pending")
    note = Column(Text, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AutomationTemplate(Base):
    __tablename__ = "automation_templates"
    __table_args__ = (Index("ix_automation_templates_user_created", "user_id", "created_at"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    workflow_definition = Column(JSON, nullable=False)
    settings = Column(JSON, nullable=False, default=dict)
    is_system = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AutomationNotification(Base):
    __tablename__ = "automation_notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    execution_id = Column(
        Integer, ForeignKey("automation_executions.id", ondelete="CASCADE"), nullable=True
    )
    channel = Column(String(24), nullable=False, server_default="in_app")
    title = Column(String(220), nullable=False)
    message = Column(Text, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
