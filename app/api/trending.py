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
from app.services.openrouter_service import categorize_keyword
from app.schemas.trending import (
    TrendingKeywordsResponse,
    TrendingKeywordResponse,
    SetBlogWorkflowRequest,
)
from app.workflows.trending_workflow import create_trending_workflow
import asyncio

router = APIRouter()


@router.get("/keywords", response_model=TrendingKeywordsResponse)
async def get_trending_keywords(
    refresh: bool = False,
    hours: int = 4,
    category: Optional[str] = None,
    completed_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get trending keywords (last 4 hours by default), optionally filtered by category.
    If completed_only=true, returns all keywords that have a blog generated (no time limit)."""
    try:
        if completed_only:
            # Return all keywords that have a blog (permanent list, no time filter)
            cached_data = get_used_trending_keywords_from_db(db, category)
            keywords = []
            for k in cached_data:
                keyword_data = k.get("data", {}) if isinstance(k.get("data"), dict) else {}
                keyword_response = TrendingKeywordResponse(
                    keyword=k.get("keyword", ""),
                    value=keyword_data.get("value") if isinstance(keyword_data, dict) else None,
                    timestamp=keyword_data.get("timestamp") if isinstance(keyword_data, dict) else None,
                    fetched_at=k.get("fetched_at"),
                    category=k.get("category"),
                    blog_workflow_id=k.get("blog_workflow_id"),
                )
                keyword_dict = keyword_response.model_dump()
                keyword_dict["id"] = k.get("id")
                keyword_dict["blog_workflow_id"] = k.get("blog_workflow_id")
                keywords.append(keyword_dict)
            return TrendingKeywordsResponse(keywords=keywords, count=len(keywords))

        if refresh:
            # Fetch fresh data from SerpAPI
            keywords_data = await fetch_trending_keywords(db, hours)
            # Get the IDs from database after fetching
            cached_data = get_trending_keywords_from_db(db, hours, category)
            keyword_id_map = {k.get("keyword"): k.get("id") for k in cached_data if k.get("keyword")}
            
            keywords = []
            for k in keywords_data:
                keyword_response = TrendingKeywordResponse(
                    keyword=k.get("keyword", ""),
                    value=k.get("value"),
                    timestamp=k.get("timestamp"),
                    category=k.get("category"),
                    blog_workflow_id=k.get("blog_workflow_id"),
                )
                keyword_dict = keyword_response.model_dump()
                keyword_dict["id"] = keyword_id_map.get(k.get("keyword", ""))
                keyword_dict["blog_workflow_id"] = k.get("blog_workflow_id")
                keywords.append(keyword_dict)
        else:
            # Get from database cache
            cached_data = get_trending_keywords_from_db(db, hours, category)
            keywords = []
            for k in cached_data:
                keyword_data = k.get("data", {}) if isinstance(k.get("data"), dict) else {}
                # Create response with ID and blog_workflow_id
                keyword_response = TrendingKeywordResponse(
                    keyword=k.get("keyword", ""),
                    value=keyword_data.get("value") if isinstance(keyword_data, dict) else None,
                    timestamp=keyword_data.get("timestamp") if isinstance(keyword_data, dict) else None,
                    fetched_at=k.get("fetched_at"),
                    category=k.get("category"),
                    blog_workflow_id=k.get("blog_workflow_id"),
                )
                keyword_dict = keyword_response.model_dump()
                keyword_dict["id"] = k.get("id")
                keyword_dict["blog_workflow_id"] = k.get("blog_workflow_id")
                keywords.append(keyword_dict)
        
        return TrendingKeywordsResponse(
            keywords=keywords,
            count=len(keywords)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching trending keywords: {str(e)}"
        )


@router.post("/keywords/fetch")
async def fetch_keywords(
    hours: int = 4,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch trending keywords from SerpAPI and return from database (last N hours)"""
    try:
        # Fetch from SerpAPI and store in database, then return from database
        # This ensures we get accurate "last N hours" data from stored records
        keywords_data = await fetch_trending_keywords(db, hours)
        
        # Convert database results to response format
        keywords = []
        for k in keywords_data:
            keyword_data = k.get("data", {}) if isinstance(k.get("data"), dict) else {}
            keyword_response = TrendingKeywordResponse(
                keyword=k.get("keyword", ""),
                value=keyword_data.get("value") if isinstance(keyword_data, dict) else None,
                timestamp=keyword_data.get("timestamp") if isinstance(keyword_data, dict) else k.get("fetched_at"),
                category=k.get("category"),
                blog_workflow_id=k.get("blog_workflow_id"),
            )
            keyword_dict = keyword_response.model_dump()
            keyword_dict["id"] = k.get("id")
            keyword_dict["blog_workflow_id"] = k.get("blog_workflow_id")
            keywords.append(keyword_dict)
        
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
    keyword.blog_workflow_id = body.workflow_id
    db.commit()
    db.refresh(keyword)
    return {
        "message": "Blog workflow linked to keyword",
        "keyword_id": keyword_id,
        "workflow_id": body.workflow_id,
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
    """Categorize all keywords (or uncategorized keywords) using LLM"""
    try:
        # Fetch keywords to categorize
        if recategorize:
            # Get all keywords
            keywords = db.query(TrendingKeywords).all()
        else:
            # Get only uncategorized keywords
            keywords = db.query(TrendingKeywords).filter(
                TrendingKeywords.category.is_(None)
            ).all()
        
        if not keywords:
            return {
                "message": "No keywords to categorize",
                "total": 0,
                "categorized": 0,
                "failed": 0
            }
        
        # Process keywords in batches
        batch_size = 15  # Process 15 keywords at a time
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
            
            # Commit after each batch
            db.commit()
        
        # Return categorized keywords
        categorized_keywords = []
        for k in keywords:
            if k.category:
                keyword_data = k.data if isinstance(k.data, dict) else {}
                keyword_response = TrendingKeywordResponse(
                    keyword=k.keyword,
                    value=keyword_data.get("value") if isinstance(keyword_data, dict) else None,
                    timestamp=keyword_data.get("timestamp") if isinstance(keyword_data, dict) else None,
                    fetched_at=k.fetched_at.isoformat() if k.fetched_at else None,
                    category=k.category,
                    blog_workflow_id=k.blog_workflow_id,
                )
                keyword_dict = keyword_response.model_dump()
                keyword_dict["id"] = k.id
                keyword_dict["blog_workflow_id"] = k.blog_workflow_id
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
