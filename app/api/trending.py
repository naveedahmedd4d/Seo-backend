from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.trending_keywords import TrendingKeywords
from app.services.serpapi_service import (
    fetch_trending_keywords,
    get_trending_keywords_from_db,
    get_used_trending_keywords_from_db,
)
from app.services.manual_keyword_service import (
    create_manual_keywords,
    get_manual_keywords_from_db,
    SOURCE_MANUAL,
    SOURCE_TRENDING,
)
from app.services.openrouter_service import categorize_keyword
from app.schemas.trending import (
    TrendingKeywordsResponse,
    TrendingKeywordResponse,
    AddManualKeywordsRequest,
    SetBlogWorkflowRequest,
)

router = APIRouter()


def _build_keyword_dict(k: dict) -> dict:
    keyword_data = k.get("data", {}) if isinstance(k.get("data"), dict) else {}
    keyword_response = TrendingKeywordResponse(
        keyword=k.get("keyword", ""),
        value=keyword_data.get("value") if isinstance(keyword_data, dict) else k.get("value"),
        timestamp=keyword_data.get("timestamp") if isinstance(keyword_data, dict) else k.get("timestamp"),
        fetched_at=k.get("fetched_at"),
        category=k.get("category"),
        bio_blog_workflow_id=k.get("bio_blog_workflow_id"),
        net_worth_blog_workflow_id=k.get("net_worth_blog_workflow_id"),
        source=k.get("source"),
    )
    keyword_dict = keyword_response.model_dump()
    keyword_dict["id"] = k.get("id")
    keyword_dict["bio_blog_workflow_id"] = k.get("bio_blog_workflow_id")
    keyword_dict["net_worth_blog_workflow_id"] = k.get("net_worth_blog_workflow_id")
    keyword_dict["source"] = k.get("source")
    return keyword_dict


@router.get("/keywords", response_model=TrendingKeywordsResponse)
async def get_trending_keywords(
    refresh: bool = False,
    hours: int = 4,
    category: Optional[str] = None,
    completed_only: bool = False,
    source: str = SOURCE_TRENDING,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get trending or manual keywords, optionally filtered by category."""
    if source not in (SOURCE_TRENDING, SOURCE_MANUAL):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source must be 'trending' or 'manual'",
        )
    try:
        if source == SOURCE_MANUAL:
            cached_data = get_manual_keywords_from_db(db, completed_only, category)
            keywords = [_build_keyword_dict(k) for k in cached_data]
            return TrendingKeywordsResponse(keywords=keywords, count=len(keywords))

        if completed_only:
            cached_data = get_used_trending_keywords_from_db(db, category)
            keywords = [_build_keyword_dict(k) for k in cached_data]
            return TrendingKeywordsResponse(keywords=keywords, count=len(keywords))

        if refresh:
            keywords_data = await fetch_trending_keywords(db, hours)
            cached_data = get_trending_keywords_from_db(db, hours, category)
            keyword_id_map = {k.get("keyword"): k.get("id") for k in cached_data if k.get("keyword")}

            keywords = []
            for k in keywords_data:
                keyword_dict = _build_keyword_dict(k)
                keyword_dict["id"] = keyword_id_map.get(k.get("keyword", ""))
                keywords.append(keyword_dict)
        else:
            cached_data = get_trending_keywords_from_db(db, hours, category)
            keywords = [_build_keyword_dict(k) for k in cached_data]

        return TrendingKeywordsResponse(
            keywords=keywords,
            count=len(keywords)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching trending keywords: {str(e)}"
        )


@router.post("/keywords/manual", response_model=TrendingKeywordsResponse)
async def add_manual_keywords(
    body: AddManualKeywordsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add one or more manual keywords (one per list item)."""
    try:
        created, added, skipped = create_manual_keywords(db, body.keywords, body.category)
        if added == 0 and skipped > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="All keywords already exist in manual list",
            )
        keywords = [_build_keyword_dict(k) for k in created]
        return TrendingKeywordsResponse(keywords=keywords, count=added)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error adding manual keywords: {str(e)}",
        )


@router.post("/keywords/fetch")
async def fetch_keywords(
    hours: int = 4,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch trending keywords from SerpAPI and return from database (last N hours)"""
    try:
        keywords_data = await fetch_trending_keywords(db, hours)
        keywords = [_build_keyword_dict(k) for k in keywords_data]
        return TrendingKeywordsResponse(
            keywords=keywords,
            count=len(keywords)
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching trending keywords: {str(e)}"
        )


@router.patch("/keywords/{keyword_id}/blog-workflow")
async def set_keyword_blog_workflow(
    keyword_id: int,
    body: SetBlogWorkflowRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set the blog workflow ID for a trending keyword (mark as completed)."""
    keyword = db.query(TrendingKeywords).filter(TrendingKeywords.id == keyword_id).first()
    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword not found",
        )
    if body.content_type == "biography":
        keyword.bio_blog_workflow_id = body.workflow_id
    else:
        keyword.net_worth_blog_workflow_id = body.workflow_id
    db.commit()
    db.refresh(keyword)
    return {
        "message": "Blog workflow linked to keyword",
        "keyword_id": keyword_id,
        "workflow_id": body.workflow_id,
        "content_type": body.content_type,
    }


@router.delete("/keywords/{keyword_id}")
async def delete_keyword(
    keyword_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a trending keyword"""
    try:
        keyword = db.query(TrendingKeywords).filter(TrendingKeywords.id == keyword_id).first()
        if not keyword:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Keyword not found"
            )
        db.delete(keyword)
        db.commit()
        return {"message": "Keyword deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting keyword: {str(e)}"
        )


@router.post("/keywords/categorize")
async def categorize_keywords(
    recategorize: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Categorize trending keywords (not manual) using LLM"""
    try:
        if recategorize:
            keywords = db.query(TrendingKeywords).filter(
                TrendingKeywords.source == SOURCE_TRENDING
            ).all()
        else:
            keywords = db.query(TrendingKeywords).filter(
                TrendingKeywords.category.is_(None),
                TrendingKeywords.source == SOURCE_TRENDING,
            ).all()

        if not keywords:
            return {
                "message": "No keywords to categorize",
                "total": 0,
                "categorized": 0,
                "failed": 0
            }

        batch_size = 15
        categorized_count = 0
        failed_count = 0

        for i in range(0, len(keywords), batch_size):
            batch = keywords[i:i + batch_size]

            for keyword_obj in batch:
                try:
                    category = await categorize_keyword(keyword_obj.keyword)
                    if category:
                        keyword_obj.category = category
                        categorized_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    import traceback
                    error_details = traceback.format_exc()
                    print(f"Error categorizing keyword '{keyword_obj.keyword}': {str(e)}")
                    print(f"Full traceback: {error_details}")
                    failed_count += 1

            db.commit()

        categorized_keywords = []
        for k in keywords:
            if k.category:
                keyword_data = k.data if isinstance(k.data, dict) else {}
                keyword_dict = _build_keyword_dict({
                    "id": k.id,
                    "keyword": k.keyword,
                    "data": keyword_data,
                    "fetched_at": k.fetched_at.isoformat() if k.fetched_at else None,
                    "category": k.category,
                    "bio_blog_workflow_id": k.bio_blog_workflow_id,
                    "net_worth_blog_workflow_id": k.net_worth_blog_workflow_id,
                    "source": k.source,
                })
                categorized_keywords.append(keyword_dict)

        return {
            "message": f"Categorized {categorized_count} keywords successfully",
            "total": len(keywords),
            "categorized": categorized_count,
            "failed": failed_count,
            "keywords": categorized_keywords
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error categorizing keywords: {str(e)}"
        )


@router.get("/keywords/history")
async def get_keyword_history(
    hours: int = 24,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get keyword history"""
    try:
        cached_data = get_trending_keywords_from_db(db, hours)
        return {
            "keywords": cached_data,
            "count": len(cached_data)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching keyword history: {str(e)}"
        )
