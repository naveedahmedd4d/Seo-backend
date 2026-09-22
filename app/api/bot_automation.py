from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload

from app.api.auth import get_current_user
from app.database import get_db
from app.models.bot_automation import (
    AutomationApproval,
    AutomationBot,
    AutomationExecution,
    AutomationNodeExecution,
    AutomationTemplate,
)
from app.models.user import User
from app.schemas.bot_automation import (
    ApprovalDecision,
    BotCreate,
    BotListResponse,
    BotResponse,
    BotStatusUpdate,
    BotUpdate,
    ExecutionListResponse,
    ExecutionResponse,
    ExecutionStart,
    NodeTestRequest,
    TemplateCreate,
    TemplateBotCreate,
    TemplateResponse,
)
from app.services.bot_automation_service import (
    create_execution,
    default_workflow_definition,
    ensure_site,
    make_idempotency_key,
    next_run_at,
    run_execution_sync,
    validate_workflow,
)

router = APIRouter()


def _bot(db: Session, user_id: int, bot_id: int) -> AutomationBot:
    row = db.query(AutomationBot).filter(
        AutomationBot.id == bot_id, AutomationBot.user_id == user_id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Bot not found")
    return row


def _execution(db: Session, user_id: int, execution_id: int) -> AutomationExecution:
    row = db.query(AutomationExecution).options(
        selectinload(AutomationExecution.node_executions),
        selectinload(AutomationExecution.events),
    ).filter(
        AutomationExecution.id == execution_id,
        AutomationExecution.user_id == user_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Execution not found")
    row.node_executions.sort(key=lambda item: item.position)
    row.events.sort(key=lambda item: item.created_at)
    return row


@router.get("/defaults")
def get_defaults(current_user: User = Depends(get_current_user)):
    return {"workflow_definition": default_workflow_definition()}


@router.get("/bots", response_model=BotListResponse)
def list_bots(
    bot_status: Optional[str] = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(AutomationBot).filter(AutomationBot.user_id == current_user.id)
    if bot_status:
        query = query.filter(AutomationBot.status == bot_status)
    items = query.order_by(AutomationBot.updated_at.desc()).all()
    return BotListResponse(items=items, total=len(items))


@router.post("/bots", response_model=BotResponse, status_code=status.HTTP_201_CREATED)
def create_bot(
    body: BotCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.wordpress_site_id is not None:
        ensure_site(db, current_user.id, body.wordpress_site_id)
    graph = (body.workflow_definition.model_dump(mode="json") if body.workflow_definition else default_workflow_definition())
    validate_workflow(graph)
    bot = AutomationBot(
        user_id=current_user.id,
        wordpress_site_id=body.wordpress_site_id,
        name=body.name.strip(),
        description=body.description,
        workflow_definition=graph,
        settings=body.settings,
        schedule_config=body.schedule_config or {"mode": "manual"},
        timezone=body.timezone,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot


@router.get("/bots/{bot_id}", response_model=BotResponse)
def get_bot(bot_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _bot(db, current_user.id, bot_id)


@router.patch("/bots/{bot_id}", response_model=BotResponse)
def update_bot(
    bot_id: int,
    body: BotUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bot = _bot(db, current_user.id, bot_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("wordpress_site_id") is not None:
        ensure_site(db, current_user.id, changes["wordpress_site_id"])
    if body.workflow_definition is not None:
        changes["workflow_definition"] = body.workflow_definition.model_dump(mode="json")
        validate_workflow(changes["workflow_definition"])
        trigger = next((node for node in changes["workflow_definition"]["nodes"] if node.get("type") == "trigger"), None)
        if trigger and "schedule_config" not in changes:
            changes["schedule_config"] = trigger.get("config") or {"mode": "manual"}
    if "schedule_config" in changes and "workflow_definition" not in changes:
        graph = {**bot.workflow_definition, "nodes": [dict(node) for node in bot.workflow_definition.get("nodes", [])]}
        for node in graph["nodes"]:
            if node.get("type") == "trigger":
                node["config"] = changes["schedule_config"]
                break
        changes["workflow_definition"] = graph
    for key, value in changes.items():
        setattr(bot, key, value)
    bot.version += 1
    if bot.status == "active":
        bot.next_run_at = next_run_at(bot.schedule_config or {}, timezone_name=bot.timezone)
    db.commit()
    db.refresh(bot)
    return bot


@router.post("/bots/{bot_id}/status", response_model=BotResponse)
def update_bot_status(
    bot_id: int,
    body: BotStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bot = _bot(db, current_user.id, bot_id)
    bot.status = body.status
    bot.next_run_at = next_run_at(bot.schedule_config or {}, timezone_name=bot.timezone) if body.status == "active" else None
    db.commit()
    db.refresh(bot)
    return bot


@router.post("/bots/{bot_id}/duplicate", response_model=BotResponse, status_code=201)
def duplicate_bot(bot_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    source = _bot(db, current_user.id, bot_id)
    duplicate = AutomationBot(
        user_id=current_user.id,
        wordpress_site_id=source.wordpress_site_id,
        name=f"{source.name} (copy)",
        description=source.description,
        status="draft",
        workflow_definition=source.workflow_definition,
        settings=source.settings,
        schedule_config=source.schedule_config,
        timezone=source.timezone,
    )
    db.add(duplicate)
    db.commit()
    db.refresh(duplicate)
    return duplicate


@router.delete("/bots/{bot_id}", status_code=204)
def delete_bot(bot_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bot = _bot(db, current_user.id, bot_id)
    db.delete(bot)
    db.commit()


@router.post("/bots/{bot_id}/run", response_model=ExecutionResponse, status_code=202)
def run_bot(
    bot_id: int,
    body: ExecutionStart,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bot = _bot(db, current_user.id, bot_id)
    execution = create_execution(
        db, bot, trigger_type="manual", start_node_id=body.start_node_id,
        idempotency_key=make_idempotency_key(),
    )
    background_tasks.add_task(run_execution_sync, execution.id)
    return _execution(db, current_user.id, execution.id)


@router.post("/bots/{bot_id}/nodes/{node_id}/test", response_model=ExecutionResponse, status_code=202)
def test_node(
    bot_id: int,
    node_id: str,
    body: NodeTestRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bot = _bot(db, current_user.id, bot_id)
    nodes = bot.workflow_definition.get("nodes", [])
    if node_id not in {str(node.get("id")) for node in nodes}:
        raise HTTPException(status_code=404, detail="Workflow node not found")
    execution = create_execution(
        db, bot, trigger_type="node_test", start_node_id=node_id,
        idempotency_key=make_idempotency_key("node-test"),
    )
    execution.settings_snapshot = {
        **(execution.settings_snapshot or {}),
        "__node_test_inputs": {node_id: body.input_data},
    }
    for row in execution.node_executions:
        if row.node_id != node_id and row.status == "pending":
            row.status = "skipped"
    db.commit()
    background_tasks.add_task(run_execution_sync, execution.id)
    return _execution(db, current_user.id, execution.id)


@router.get("/executions", response_model=ExecutionListResponse)
def list_executions(
    bot_id: Optional[int] = None,
    execution_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(AutomationExecution).options(
        selectinload(AutomationExecution.node_executions),
        selectinload(AutomationExecution.events),
    ).filter(AutomationExecution.user_id == current_user.id)
    if bot_id:
        query = query.filter(AutomationExecution.bot_id == bot_id)
    if execution_status:
        query = query.filter(AutomationExecution.status == execution_status)
    items = query.order_by(AutomationExecution.created_at.desc()).limit(limit).all()
    for item in items:
        item.node_executions.sort(key=lambda row: row.position)
        item.events.sort(key=lambda row: row.created_at)
    return ExecutionListResponse(items=items, total=len(items))


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
def get_execution(execution_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _execution(db, current_user.id, execution_id)


@router.post("/executions/{execution_id}/restart", response_model=ExecutionResponse, status_code=202)
def restart_execution(
    execution_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    failed = _execution(db, current_user.id, execution_id)
    if failed.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed executions can be restarted")
    failed_node = next((row.node_id for row in failed.node_executions if row.status == "failed"), None)
    if not failed_node:
        raise HTTPException(status_code=409, detail="No failed node was found")
    bot = _bot(db, current_user.id, failed.bot_id)
    execution = create_execution(
        db, bot, trigger_type="restart", start_node_id=failed_node,
        resumed_from=failed, idempotency_key=make_idempotency_key("restart"),
    )
    background_tasks.add_task(run_execution_sync, execution.id)
    return _execution(db, current_user.id, execution.id)


@router.post("/executions/{execution_id}/approval", response_model=ExecutionResponse)
def decide_approval(
    execution_id: int,
    body: ApprovalDecision,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    execution = _execution(db, current_user.id, execution_id)
    if execution.status != "waiting_approval" or not execution.current_node_id:
        raise HTTPException(status_code=409, detail="Execution is not waiting for approval")
    approval = db.query(AutomationApproval).filter(
        AutomationApproval.execution_id == execution.id,
        AutomationApproval.user_id == current_user.id,
        AutomationApproval.node_id == execution.current_node_id,
    ).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    approval.status = "approved" if body.approved else "rejected"
    approval.note = body.note
    approval.decided_at = datetime.now(timezone.utc)
    node = db.query(AutomationNodeExecution).filter(
        AutomationNodeExecution.execution_id == execution.id,
        AutomationNodeExecution.node_id == execution.current_node_id,
    ).first()
    if body.approved:
        execution.status = "queued"
        node.status = "pending"
        db.commit()
        background_tasks.add_task(run_execution_sync, execution.id)
    else:
        execution.status = "failed"
        execution.error_summary = "Publication approval rejected"
        execution.completed_at = datetime.now(timezone.utc)
        node.status = "failed"
        node.error_data = {"message": execution.error_summary}
        db.commit()
    return _execution(db, current_user.id, execution.id)


@router.get("/templates", response_model=List[TemplateResponse])
def list_templates(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(AutomationTemplate).filter(
        AutomationTemplate.user_id == current_user.id
    ).order_by(AutomationTemplate.updated_at.desc()).all()


@router.post("/templates", response_model=TemplateResponse, status_code=201)
def create_template(
    body: TemplateCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.bot_id:
        bot = _bot(db, current_user.id, body.bot_id)
        graph, template_settings = bot.workflow_definition, bot.settings
    elif body.workflow_definition:
        graph = body.workflow_definition.model_dump(mode="json")
        template_settings = body.settings
    else:
        raise HTTPException(status_code=422, detail="bot_id or workflow_definition is required")
    validate_workflow(graph)
    template = AutomationTemplate(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        workflow_definition=graph,
        settings=template_settings,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.post("/templates/{template_id}/bots", response_model=BotResponse, status_code=201)
def create_bot_from_template(
    template_id: int,
    body: TemplateBotCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = db.query(AutomationTemplate).filter(
        AutomationTemplate.id == template_id,
        AutomationTemplate.user_id == current_user.id,
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if body.wordpress_site_id is not None:
        ensure_site(db, current_user.id, body.wordpress_site_id)
    bot = AutomationBot(
        user_id=current_user.id,
        wordpress_site_id=body.wordpress_site_id,
        name=body.name or template.name,
        description=body.description if body.description is not None else template.description,
        status="draft",
        workflow_definition=template.workflow_definition,
        settings=template.settings,
        schedule_config={"mode": "manual"},
        timezone="UTC",
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot
