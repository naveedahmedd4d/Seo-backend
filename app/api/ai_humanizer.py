from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.ai_humanizer import HumanizeRequest, HumanizeResponse
from app.services.humanizer.pipeline import humanize_pipeline
from app.services.llm_settings_service import resolve_api_key

router = APIRouter()


@router.post("/humanize", response_model=HumanizeResponse)
async def humanize(
    request: HumanizeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Humanize text using Apify Content Humanizer (akash9078/ai-content-humanizer).
    """
    try:
        apify_key = resolve_api_key(db, current_user, "apify")
        return await humanize_pipeline(request.text, tone=request.tone, api_key=apify_key)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Humanize failed: {str(e)}",
        )
