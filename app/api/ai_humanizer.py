from fastapi import APIRouter, HTTPException, status

from app.schemas.ai_humanizer import HumanizeRequest, HumanizeResponse
from app.services.humanizer.pipeline import humanize_pipeline

router = APIRouter()


@router.post("/humanize", response_model=HumanizeResponse)
async def humanize(request: HumanizeRequest):
    """
    Humanize text using Apify Content Humanizer (akash9078/ai-content-humanizer).

    Requires APIFY_API_KEY. See https://apify.com/akash9078/ai-content-humanizer
    """
    try:
        return await humanize_pipeline(request.text, tone=request.tone)
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
