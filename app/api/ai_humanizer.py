from fastapi import APIRouter, HTTPException, status

from app.schemas.ai_humanizer import HumanizeRequest, HumanizeResponse
from app.services.humanizer.pipeline import humanize_pipeline

router = APIRouter()


@router.post("/humanize", response_model=HumanizeResponse)
async def humanize(request: HumanizeRequest):
    """Three-stage humanizer: pattern analysis, replacement + LLM rewrite, grammar polish."""
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
