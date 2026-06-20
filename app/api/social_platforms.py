from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.models.user import User
from app.schemas.social_platform import SocialPlatformListResponse, SocialPlatformResponse
from app.services.social_platform_registry import get_social_platforms

router = APIRouter()


@router.get("", response_model=SocialPlatformListResponse)
async def list_social_platforms(
    category: Optional[str] = Query(default=None, description="Filter by category"),
    content_type: Optional[str] = Query(default=None, description="Filter by content type"),
    current_user: User = Depends(get_current_user),
):
    """List available social platforms from the dynamic registry."""
    platforms = get_social_platforms(category=category, content_type=content_type)
    return SocialPlatformListResponse(
        platforms=platforms,
        total=len(platforms),
    )
