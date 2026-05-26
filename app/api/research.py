from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.services.trivly_service import (
    research_keywords,
    get_content_suggestions,
    get_research_history,
)
from app.schemas.research import (
    KeywordResearchRequest,
    KeywordResearchResponse,
    ContentSuggestionRequest,
    ContentSuggestionResponse,
    ResearchHistoryResponse,
)
from app.workflows.research_workflow import create_research_workflow

router = APIRouter()


@router.post("/keywords", response_model=KeywordResearchResponse)
async def research_keywords_endpoint(
    request: KeywordResearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Research keywords via Tavily"""
    try:
        results = await research_keywords(db, request.query, current_user.id)
        return KeywordResearchResponse(
            query=request.query,
            results=results,
            created_at=datetime.utcnow().isoformat()
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error researching keywords: {str(e)}"
        )


@router.post("/content", response_model=ContentSuggestionResponse)
async def get_content_suggestions_endpoint(
    request: ContentSuggestionRequest,
    current_user: User = Depends(get_current_user)
):
    """Get content suggestions"""
    try:
        suggestions = await get_content_suggestions(
            request.query,
            request.num_suggestions
        )
        return ContentSuggestionResponse(suggestions=suggestions)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting content suggestions: {str(e)}"
        )


@router.get("/history", response_model=List[ResearchHistoryResponse])
async def get_research_history_endpoint(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get research history"""
    try:
        history = get_research_history(db, current_user.id, limit)
        # Convert SQLAlchemy models to Pydantic models with proper serialization
        response_items = []
        for item in history:
            try:
                # Handle results field - ensure it's a dict or None
                results_data = item.results
                if results_data is not None:
                    # If results is a string (shouldn't happen with JSON type, but handle it)
                    if isinstance(results_data, str):
                        import json
                        results_data = json.loads(results_data)
                    # Ensure it's a dict
                    if not isinstance(results_data, dict):
                        results_data = None
                
                # Handle created_at - convert datetime to ISO string
                created_at_str = datetime.utcnow().isoformat()
                if item.created_at:
                    if hasattr(item.created_at, 'isoformat'):
                        created_at_str = item.created_at.isoformat()
                    else:
                        created_at_str = str(item.created_at)
                
                response_items.append(
                    ResearchHistoryResponse(
                        id=item.id,
                        query=item.query,
                        results=results_data,
                        created_at=created_at_str
                    )
                )
            except Exception as item_error:
                # Log error for this item but continue with others
                import logging
                logging.error(f"Error serializing research history item {item.id}: {str(item_error)}")
                continue
        
        return response_items
    except Exception as e:
        import traceback
        import logging
        logging.error(f"Error fetching research history: {str(e)}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching research history: {str(e)}"
        )

