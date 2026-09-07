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
    TopicalMappingResponse,
    AutocompleteSuggestion,
    RelatedQueryItem,
    ResearchSummaryRequest,
    ResearchSummaryResponse,
    HeadingNode,
    NewsSourceItem,
)
from app.services.serpapi_service import fetch_topical_mapping
from app.services.openrouter_service import (
    generate_research_content_summary,
    set_llm_context,
    reset_llm_context,
)
from app.services.llm_settings_service import resolve_api_key, resolve_llm_credentials
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
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error researching keywords: {str(e)}"
        )


@router.post("/content", response_model=ContentSuggestionResponse)
async def get_content_suggestions_endpoint(
    request: ContentSuggestionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get content suggestions"""
    try:
        tavily_key = resolve_api_key(db, current_user, "tavily")
        suggestions = await get_content_suggestions(
            request.query,
            request.num_suggestions,
            api_key=tavily_key,
        )
        return ContentSuggestionResponse(suggestions=suggestions)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting content suggestions: {str(e)}"
        )


@router.post("/generate-summary", response_model=ResearchSummaryResponse)
async def generate_research_summary_endpoint(
    request: ResearchSummaryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate AI research summary and structured content outline for blog generation."""
    if not request.query or not request.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query is required",
        )
    if not request.results:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Research results are required. Run keyword research first.",
        )
    llm_token = None
    try:
        creds = resolve_llm_credentials(db, current_user.id)
        llm_token = set_llm_context(creds)
        topical_dict = None
        if request.topical_mapping:
            topical_dict = request.topical_mapping.model_dump()
        competitors = [c.model_dump() for c in (request.competitors or [])]
        opportunities = [o.model_dump() for o in (request.opportunities or [])]
        data = await generate_research_content_summary(
            query=request.query.strip(),
            results=request.results,
            topical_mapping=topical_dict,
            competitors=competitors,
            opportunities=opportunities,
            category=request.category,
            celebrity_content_type=request.celebrity_content_type,
            content_intent=request.content_intent,
            cluster_subs=request.cluster_subs,
        )
        outline = [HeadingNode(**node) for node in data.get("outline") or []]
        news_sources_raw = data.get("news_sources") or []
        news_sources = [
            NewsSourceItem(**item) if isinstance(item, dict) else NewsSourceItem()
            for item in news_sources_raw
        ] if news_sources_raw else None
        return ResearchSummaryResponse(
            summary=data.get("summary") or "",
            h1=data.get("h1") or request.query.strip(),
            outline=outline,
            key_topics=data.get("key_topics") or [],
            subtopics=data.get("subtopics") or [],
            core_content_recommendations=data.get("core_content_recommendations") or [],
            seo_suggestions=data.get("seo_suggestions") or [],
            topical_coverage=data.get("topical_coverage") or [],
            target_keywords=data.get("target_keywords") or [],
            section_notes=data.get("section_notes"),
            category=data.get("category") or request.category,
            celebrity_content_type=data.get("celebrity_content_type") or request.celebrity_content_type,
            outline_template=data.get("outline_template"),
            news_headline=data.get("news_headline"),
            news_hook_paragraph=data.get("news_hook_paragraph"),
            news_meta_description=data.get("news_meta_description"),
            news_hashtags=data.get("news_hashtags"),
            news_sources=news_sources,
            news_context=data.get("news_context"),
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating research summary: {str(e)}",
        )
    finally:
        if llm_token is not None:
            reset_llm_context(llm_token)


@router.get("/topical-mapping", response_model=TopicalMappingResponse)
async def get_topical_mapping_endpoint(
    q: str,
    limit: int = 8,
    geo: str = "US",
    hl: str = "en",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Topical mapping: Google Autocomplete + Google Trends related queries (SerpAPI)."""
    if not q or not q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'q' is required",
        )
    try:
        serp_key = resolve_api_key(db, current_user, "serpapi")
        data = await fetch_topical_mapping(
            q.strip(), limit=min(limit, 20), geo=geo, hl=hl, api_key=serp_key
        )
        return TopicalMappingResponse(
            query=data["query"],
            autocomplete_suggestions=[
                AutocompleteSuggestion(**s) for s in data["autocomplete_suggestions"]
            ],
            related_queries=[
                RelatedQueryItem(**r) for r in data["related_queries"]
            ],
            errors=data.get("errors"),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching topical mapping: {str(e)}",
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

