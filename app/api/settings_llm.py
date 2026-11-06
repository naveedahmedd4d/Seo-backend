"""Per-user LLM / API settings (keys + blog model preferences + fallback lineup)."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.settings_llm import (
    LlmModelsResponse,
    LlmSettingsResponse,
    LlmSettingsUpdateRequest,
    LlmTestRequest,
    LlmTestResponse,
)
from app.services.http_ssl import httpx_verify
from app.services.llm_settings_service import (
    get_model_allowlist,
    get_or_create_settings,
    get_saved_provider_key,
    to_public_response,
    upsert_settings,
)

router = APIRouter()


@router.get("/llm", response_model=LlmSettingsResponse)
async def get_llm_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = get_or_create_settings(db, current_user.id)
    return to_public_response(row, user=current_user)


@router.put("/llm", response_model=LlmSettingsResponse)
async def update_llm_settings(
    body: LlmSettingsUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        row = upsert_settings(
            db,
            current_user.id,
            gemini_api_key=body.gemini_api_key,
            openrouter_api_key=body.openrouter_api_key,
            groq_api_key=body.groq_api_key,
            cloudflare_api_token=body.cloudflare_api_token,
            cloudflare_account_id=body.cloudflare_account_id,
            mistral_api_key=body.mistral_api_key,
            cohere_api_key=body.cohere_api_key,
            nvidia_api_key=body.nvidia_api_key,
            trending_fallback_groq_api_key=body.trending_fallback_groq_api_key,
            blog_fallback_groq_api_key=body.blog_fallback_groq_api_key,
            serpapi_api_key=body.serpapi_api_key,
            tavily_api_key=body.tavily_api_key,
            mangools_api_token=body.mangools_api_token,
            apify_api_key=body.apify_api_key,
            clear_gemini_api_key=body.clear_gemini_api_key,
            clear_openrouter_api_key=body.clear_openrouter_api_key,
            clear_groq_api_key=body.clear_groq_api_key,
            clear_cloudflare_api_token=body.clear_cloudflare_api_token,
            clear_mistral_api_key=body.clear_mistral_api_key,
            clear_cohere_api_key=body.clear_cohere_api_key,
            clear_nvidia_api_key=body.clear_nvidia_api_key,
            clear_trending_fallback_groq_api_key=body.clear_trending_fallback_groq_api_key,
            clear_blog_fallback_groq_api_key=body.clear_blog_fallback_groq_api_key,
            clear_serpapi_api_key=body.clear_serpapi_api_key,
            clear_tavily_api_key=body.clear_tavily_api_key,
            clear_mangools_api_token=body.clear_mangools_api_token,
            clear_apify_api_key=body.clear_apify_api_key,
            preferred_provider=body.preferred_provider,
            preferred_model=body.preferred_model,
            fallback_order=body.fallback_order,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return to_public_response(row, user=current_user)


@router.get("/llm/models", response_model=LlmModelsResponse)
async def list_llm_models(
    current_user: User = Depends(get_current_user),
):
    return LlmModelsResponse(models=get_model_allowlist())


@router.post("/llm/test", response_model=LlmTestResponse)
async def test_llm_key(
    body: LlmTestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    api_key = (body.api_key or "").strip() or None
    if not api_key:
        api_key = get_saved_provider_key(db, current_user.id, body.provider)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No {body.provider} API key to test. Paste a key or save one first.",
        )

    account_id = (body.account_id or "").strip() or None
    if body.provider == "cloudflare" and not account_id:
        row = get_or_create_settings(db, current_user.id)
        account_id = getattr(row, "cloudflare_account_id", None)

    if body.provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        model = "openrouter/free"
    elif body.provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        model = "gemini-2.5-flash"
    elif body.provider == "cloudflare":
        if not account_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cloudflare account id is required to test Workers AI.",
            )
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
        model = "@cf/meta/llama-3.1-8b-instruct"
    elif body.provider == "mistral":
        url = "https://api.mistral.ai/v1/chat/completions"
        model = "mistral-small-latest"
    elif body.provider == "cohere":
        url = "https://api.cohere.ai/compatibility/v1/chat/completions"
        model = "command-light"
    elif body.provider == "nvidia":
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        model = "meta/llama-3.1-8b-instruct"
    else:
        url = "https://api.groq.com/openai/v1/chat/completions"
        model = "openai/gpt-oss-20b"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK"}],
        "max_tokens": 5,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            return LlmTestResponse(ok=True, message="API key is valid.")
        try:
            err = response.json().get("error", {}).get("message", response.text)
        except Exception:
            err = response.text
        return LlmTestResponse(ok=False, message=f"Validation failed ({response.status_code}): {err}")
    except Exception as e:
        return LlmTestResponse(ok=False, message=f"Request failed: {e}")
