from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models.workflow_state import WorkflowState, WorkflowType, WorkflowStatus
from app.workflows.trending_workflow import create_trending_workflow
from app.workflows.research_workflow import create_research_workflow
from app.workflows.image_workflow import create_image_workflow
from app.workflows.blog_workflow import create_blog_workflow
import json


def get_workflow_by_type(workflow_type: WorkflowType):
    """Get workflow instance by type"""
    workflows = {
        WorkflowType.TRENDING: create_trending_workflow(),
        WorkflowType.RESEARCH: create_research_workflow(),
        WorkflowType.IMAGE: create_image_workflow(),
        WorkflowType.BLOG: create_blog_workflow(),
    }
    return workflows.get(workflow_type)


async def execute_workflow(
    db: Session,
    workflow_type: WorkflowType,
    user_id: int,
    initial_state: Dict[str, Any]
) -> WorkflowState:
    """Execute a workflow and track its state"""
    # Create workflow state record
    workflow_state = WorkflowState(
        user_id=user_id,
        workflow_type=workflow_type,
        status=WorkflowStatus.PENDING,
        state_data=initial_state
    )
    db.add(workflow_state)
    db.commit()
    db.refresh(workflow_state)
    
    try:
        # Update status to running
        workflow_state.status = WorkflowStatus.RUNNING
        db.commit()
        
        # Get workflow instance
        workflow = get_workflow_by_type(workflow_type)
        if not workflow:
            raise ValueError(f"Unknown workflow type: {workflow_type}")
        
        # Execute workflow
        result = await workflow.ainvoke(initial_state)
        
        # Update workflow state
        workflow_state.status = WorkflowStatus.COMPLETED
        workflow_state.state_data = result
        db.commit()
        
        return workflow_state
        
    except Exception as e:
        # Update workflow state with error
        workflow_state.status = WorkflowStatus.FAILED
        workflow_state.error_message = str(e)
        db.commit()
        raise


def get_workflow_state(
    db: Session,
    workflow_id: int,
    user_id: int
) -> Optional[WorkflowState]:
    """Get workflow state by ID"""
    return db.query(WorkflowState).filter(
        WorkflowState.id == workflow_id,
        WorkflowState.user_id == user_id
    ).first()


def get_user_workflows(
    db: Session,
    user_id: int,
    workflow_type: Optional[WorkflowType] = None,
    limit: int = 50
) -> list[WorkflowState]:
    """Get workflows for a user"""
    query = db.query(WorkflowState).filter(WorkflowState.user_id == user_id)
    if workflow_type:
        query = query.filter(WorkflowState.workflow_type == workflow_type)
    return query.order_by(WorkflowState.created_at.desc()).limit(limit).all()

