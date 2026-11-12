from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.image_search_history import ImageSearchHistory
from app.services.custom_image_search_service import (
    is_custom_image_search_configured,
    search_social_images,
)
from app.services.llm_settings_service import resolve_api_key_optional
from app.services.serpapi_service import search_images
from app.schemas.images import (
    ImageSearchRequest,
    ImageSearchResponse,
    ImageResult,
    ImageSearchHistoryResponse,
)

router = APIRouter()


def _image_data_to_results(images_data: list) -> list:
    """Convert raw image dicts to list of dicts for JSON storage."""
    return [
        {
            "title": img.get("title", ""),
            "link": img.get("link", ""),
            "original": img.get("original", ""),
            "thumbnail": img.get("thumbnail", ""),
            "source": img.get("source", ""),
        }
        for img in images_data
    ]


@router.post("/search", response_model=ImageSearchResponse)
async def search_images_endpoint(
    request: ImageSearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search images by keyword and save to history.

    Prefer Custom Image Search Api when configured (Social Post backgrounds);
    fall back to SerpAPI. Advanced SerpAPI filters apply only on the SerpAPI path.
    """
    try:
        serp_key = resolve_api_key_optional(db, current_user, "serpapi")
        custom_ready = is_custom_image_search_configured(db, current_user.id)
        if not custom_ready and not serp_key:
            raise ValueError(
                "Add your Custom Image Search Api or SerpAPI key in Settings"
            )

        if custom_ready:
            images_data = await search_social_images(
                db,
                current_user.id,
                request.keyword,
                num_results=request.num_results,
                serpapi_key=serp_key,
            )
        else:
            images_data = await search_images(
                request.keyword,
                request.num_results,
                source=request.source,
                size=request.size,
                color=request.color,
                orientation=request.orientation,
                api_key=serp_key,
            )
        images = [
            ImageResult(
                title=img.get("title", ""),
                link=img.get("link", ""),
                original=img.get("original", ""),
                thumbnail=img.get("thumbnail", ""),
                source=img.get("source", ""),
            )
            for img in images_data
        ]
        # Persist to database
        record = ImageSearchHistory(
            user_id=current_user.id,
            keyword=request.keyword,
            results=_image_data_to_results(images_data),
        )
        db.add(record)
        db.commit()
        return ImageSearchResponse(
            keyword=request.keyword,
            images=images,
            count=len(images),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error searching images: {str(e)}",
        )


@router.get("/history", response_model=list[ImageSearchHistoryResponse])
async def get_image_search_history(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get image search history for the current user."""
    try:
        rows = (
            db.query(ImageSearchHistory)
            .filter(ImageSearchHistory.user_id == current_user.id)
            .order_by(ImageSearchHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            ImageSearchHistoryResponse(
                id=r.id,
                keyword=r.keyword,
                results=r.results,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching image search history: {str(e)}",
        )


@router.delete("/history/{history_id}")
async def delete_image_search_history(
    history_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a single image search history entry for the current user."""
    record = (
        db.query(ImageSearchHistory)
        .filter(
            ImageSearchHistory.id == history_id,
            ImageSearchHistory.user_id == current_user.id,
        )
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image search history entry not found",
        )
    db.delete(record)
    db.commit()
    return {"ok": True}
