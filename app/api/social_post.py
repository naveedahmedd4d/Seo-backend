from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.social_post_history import SocialPostHistory
from app.services.news_research_service import (
    build_news_bundle,
    estimate_engagement_score,
    suggested_platforms_for_headline,
)
from app.schemas.social_post import (
    SocialPostGenerateRequest,
    SocialPostGenerateResponse,
    NewsResultItem,
    SocialPostHistorySaveRequest,
    SocialPostHistoryItemResponse,
)

router = APIRouter()


@router.post("/generate", response_model=SocialPostGenerateResponse)
async def generate_social_post(
    request: SocialPostGenerateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Generate a social post headline from a query: fetch 7 Google News results,
    then use the LLM to produce a single compelling headline.
    """
    try:
        bundle = await build_news_bundle(
            request.query,
            template_variant=request.template_variant,
        )
        if not bundle:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No news results found for the given query.",
            )
        items = [
            NewsResultItem(
                title=item.get("title", ""),
                link=item.get("link", ""),
                source=item.get("source", ""),
                snippet=item.get("snippet", ""),
                date=item.get("date", ""),
            )
            for item in bundle.news_results
        ]
        headline = bundle.headline
        word_count = len(headline.replace("\n", " ").split())
        char_count = len(headline)
        summary = (bundle.news_results[0].get("snippet") or "")[:100]
        return SocialPostGenerateResponse(
            headline=headline,
            news_results=items,
            word_count=word_count,
            char_count=char_count,
            estimated_engagement_score=estimate_engagement_score(headline),
            suggested_platforms=suggested_platforms_for_headline(headline),
            hashtags=bundle.hashtags,
            summary=summary,
            description=bundle.description,
            story_description=bundle.story_description,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating social post: {str(e)}",
        )


@router.get("/history", response_model=list[SocialPostHistoryItemResponse])
async def get_social_post_history(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get social post history for the current user."""
    try:
        rows = (
            db.query(SocialPostHistory)
            .filter(SocialPostHistory.user_id == current_user.id)
            .order_by(SocialPostHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            SocialPostHistoryItemResponse(
                id=r.id,
                query=r.query,
                headline=r.headline,
                description=getattr(r, "description", None),
                story_description=getattr(r, "story_description", None),
                hashtags=getattr(r, "hashtags", None),
                settings_snapshot=r.settings_snapshot,
                news_results=r.news_results,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching social post history: {str(e)}",
        )


@router.post("/history", response_model=SocialPostHistoryItemResponse)
async def save_social_post_to_history(
    request: SocialPostHistorySaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a generated social post to history (for later preview and download)."""
    try:
        record = SocialPostHistory(
            user_id=current_user.id,
            query=request.query,
            headline=request.headline,
            description=request.description,
            story_description=request.story_description,
            hashtags=request.hashtags,
            settings_snapshot=request.settings_snapshot,
            news_results=request.news_results,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return SocialPostHistoryItemResponse(
            id=record.id,
            query=record.query,
            headline=record.headline,
            description=getattr(record, "description", None),
            story_description=getattr(record, "story_description", None),
            hashtags=getattr(record, "hashtags", None),
            settings_snapshot=record.settings_snapshot,
            news_results=record.news_results,
            created_at=record.created_at.isoformat() if record.created_at else "",
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saving social post to history: {str(e)}",
        )
