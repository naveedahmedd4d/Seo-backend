from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


NodeType = Literal["trigger", "trending_keywords", "research", "blog_generation", "social_media"]


class WorkflowPosition(BaseModel):
    x: float = 0
    y: float = 0


class WorkflowNode(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    type: NodeType
    label: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    position: WorkflowPosition = Field(default_factory=WorkflowPosition)
    config: Dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    source: str
    target: str


class WorkflowDefinition(BaseModel):
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]

    @field_validator("nodes")
    @classmethod
    def unique_node_ids(cls, nodes: List[WorkflowNode]) -> List[WorkflowNode]:
        ids = [node.id for node in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Workflow node ids must be unique")
        return nodes


class BotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = None
    wordpress_site_id: Optional[int] = None
    workflow_definition: Optional[WorkflowDefinition] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    schedule_config: Dict[str, Any] = Field(default_factory=dict)
    timezone: str = Field(default="UTC", max_length=64)


class BotUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = None
    wordpress_site_id: Optional[int] = None
    workflow_definition: Optional[WorkflowDefinition] = None
    settings: Optional[Dict[str, Any]] = None
    schedule_config: Optional[Dict[str, Any]] = None
    timezone: Optional[str] = Field(default=None, max_length=64)


class BotStatusUpdate(BaseModel):
    status: Literal["draft", "active", "paused"]


class BotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    wordpress_site_id: Optional[int]
    name: str
    description: Optional[str]
    status: str
    workflow_definition: Dict[str, Any]
    settings: Dict[str, Any]
    schedule_config: Dict[str, Any]
    timezone: str
    next_run_at: Optional[datetime]
    last_run_at: Optional[datetime]
    version: int
    created_at: datetime
    updated_at: datetime


class BotListResponse(BaseModel):
    items: List[BotResponse]
    total: int


class ExecutionStart(BaseModel):
    start_node_id: Optional[str] = None


class NodeTestRequest(BaseModel):
    input_data: Dict[str, Any] = Field(default_factory=dict)


class NodeExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: str
    node_type: str
    position: int
    attempt: int
    status: str
    input_data: Optional[Dict[str, Any]]
    output_data: Optional[Dict[str, Any]]
    usage_data: Optional[Dict[str, Any]]
    error_data: Optional[Dict[str, Any]]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_execution_id: Optional[int]
    level: str
    event_type: str
    message: str
    details: Optional[Dict[str, Any]]
    created_at: datetime


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bot_id: int
    user_id: int
    wordpress_site_id: Optional[int]
    trigger_type: str
    status: str
    current_node_id: Optional[str]
    start_node_id: Optional[str]
    resumed_from_execution_id: Optional[int]
    error_summary: Optional[str]
    final_output: Optional[Dict[str, Any]]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    node_executions: List[NodeExecutionResponse] = Field(default_factory=list)
    events: List[EventResponse] = Field(default_factory=list)


class ExecutionListResponse(BaseModel):
    items: List[ExecutionResponse]
    total: int


class ApprovalDecision(BaseModel):
    approved: bool
    note: Optional[str] = None


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = None
    bot_id: Optional[int] = None
    workflow_definition: Optional[WorkflowDefinition] = None
    settings: Dict[str, Any] = Field(default_factory=dict)


class TemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    workflow_definition: Dict[str, Any]
    settings: Dict[str, Any]
    is_system: bool
    created_at: datetime
    updated_at: datetime


class TemplateBotCreate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = None
    wordpress_site_id: Optional[int] = None
