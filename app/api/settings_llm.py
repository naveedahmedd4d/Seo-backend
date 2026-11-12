"""Per-user LLM / API settings (keys + blog model preferences + fallback lineup)."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.settings_llm import (
    ClaudeLlmTestRequest,
    CustomImageSearchTestRequest,
    LlmModelsResponse,
    LlmSettingsResponse,
    LlmSettingsUpdateRequest,
    LlmTestRequest,
    LlmTestResponse,
)
from starlette.concurrency import run_in_threadpool

from app.services import llm_catalog_service
from app.services.http_ssl import httpx_verify
from app.services.llm_settings_service import (
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_COHERE_MODEL,
    DEFAULT_GEMINI_BLOG_MODEL,
    DEFAULT_MISTRAL_MODEL,
    DEFAULT_OPENROUTER_FREE_MODEL,
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
            claude_routine_url=body.claude_routine_url,
            claude_routine_token=body.claude_routine_token,
            claude_routine_callback_secret=body.claude_routine_callback_secret,
            claude_llm_api_key=body.claude_llm_api_key,
            claude_llm_base_url=body.claude_llm_base_url,
            claude_llm_webhook_secret=body.claude_llm_webhook_secret,
            claude_llm_timeout_ms=body.claude_llm_timeout_ms,
            trending_fallback_groq_api_key=body.trending_fallback_groq_api_key,
            blog_fallback_groq_api_key=body.blog_fallback_groq_api_key,
            serpapi_api_key=body.serpapi_api_key,
            custom_image_search_api_key=body.custom_image_search_api_key,
            custom_image_search_base_url=body.custom_image_search_base_url,
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
            clear_claude_routine=body.clear_claude_routine,
            clear_claude_llm=body.clear_claude_llm,
            clear_trending_fallback_groq_api_key=body.clear_trending_fallback_groq_api_key,
            clear_blog_fallback_groq_api_key=body.clear_blog_fallback_groq_api_key,
            clear_serpapi_api_key=body.clear_serpapi_api_key,
            clear_custom_image_search=body.clear_custom_image_search,
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
    db: Session = Depends(get_db),
):
    """The allowlist, annotated with what each vendor currently serves.

    Vendor catalogs go stale without notice, so every model is checked against
    the provider's own list. A provider we cannot ask (no key saved, vendor
    unreachable) leaves its models `available=None` -- unknown, not dead.
    """
    models = get_model_allowlist()

    row = get_or_create_settings(db, current_user.id)
    account_id = getattr(row, "cloudflare_account_id", None)
    providers = {}
    for provider in {m.provider for m in models} - {"default"}:
        key = get_saved_provider_key(db, current_user.id, provider)
        if key or provider in llm_catalog_service.PUBLIC_CATALOGS:
            providers[provider] = (key, account_id)

    live = await run_in_threadpool(llm_catalog_service.availability_map, providers)

    annotated = []
    for m in models:
        if m.provider == "default":
            annotated.append(m)
            continue
        ids = live.get(m.provider)
        annotated.append(m.model_copy(update={"available": None if ids is None else m.id in ids}))
    return LlmModelsResponse(models=annotated)


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
        model = DEFAULT_OPENROUTER_FREE_MODEL
    elif body.provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        model = DEFAULT_GEMINI_BLOG_MODEL
    elif body.provider == "cloudflare":
        if not account_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cloudflare account id is required to test Workers AI.",
            )
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
        model = DEFAULT_CLOUDFLARE_MODEL
    elif body.provider == "mistral":
        url = "https://api.mistral.ai/v1/chat/completions"
        model = DEFAULT_MISTRAL_MODEL
    elif body.provider == "cohere":
        url = "https://api.cohere.ai/compatibility/v1/chat/completions"
        model = DEFAULT_COHERE_MODEL
    elif body.provider == "nvidia":
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        model = "meta/llama-3.3-70b-instruct"
    else:
        url = "https://api.groq.com/openai/v1/chat/completions"
        model = "openai/gpt-oss-20b"

    # An explicit model makes this a per-model probe rather than a key check.
    # Validate it belongs to this provider so the vendor error the user sees
    # always refers to the model they actually selected.
    requested_model = (body.model or "").strip()
    if requested_model and requested_model != "default":
        allowed = {
            m.id for m in get_model_allowlist() if m.provider == body.provider
        }
        if requested_model not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Model not allowed for {body.provider}: {requested_model}"
                ),
            )
        model = requested_model

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
            return LlmTestResponse(ok=True, message=f"API key is valid. Model {model} responded.")
        try:
            err = response.json().get("error", {}).get("message", response.text)
        except Exception:
            err = response.text
        return LlmTestResponse(
            ok=False,
            message=f"{model} failed ({response.status_code}): {err}",
        )
    except Exception as e:
        return LlmTestResponse(ok=False, message=f"{model} request failed: {e}")


@router.post("/llm/test-claude-llm", response_model=LlmTestResponse)
async def test_claude_llm(
    body: ClaudeLlmTestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit one short hello job through the Claude LLM relay."""
    from app.services.claude_llm_service import (
        DEFAULT_TIMEOUT_MS,
        load_claude_llm_config,
        normalize_run_url,
        test_claude_llm_connection,
    )

    api_key = (body.api_key or "").strip() or None
    requested_base = (body.base_url or "").strip() or None
    timeout_ms = body.timeout_ms
    cfg = load_claude_llm_config(db, current_user.id)

    if not api_key:
        if not cfg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No Claude LLM API key to test. Paste a key or save one first.",
            )
        api_key = cfg.api_key

    if requested_base:
        try:
            run_url, _origin = normalize_run_url(requested_base)
        except ValueError as exc:
            return LlmTestResponse(ok=False, message=str(exc))
        # Never send a saved key to a different run URL than the one on file.
        if not (body.api_key or "").strip() and cfg and run_url != cfg.run_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paste the API key when testing a different Claude LLM URL.",
            )
        base_url = run_url
    else:
        if not cfg:
            return LlmTestResponse(
                ok=False,
                message=(
                    "Paste the full Claude LLM run URL "
                    "(…/api/v1/run/<endpoint-id>) to test."
                ),
            )
        base_url = cfg.run_url

    if timeout_ms is None:
        timeout_ms = cfg.timeout_ms if cfg else DEFAULT_TIMEOUT_MS

    ok, message = await test_claude_llm_connection(
        api_key=api_key,
        base_url=base_url,
        timeout_ms=timeout_ms,
    )
    return LlmTestResponse(ok=ok, message=message)


@router.post("/llm/test-custom-image-search", response_model=LlmTestResponse)
async def test_custom_image_search(
    body: CustomImageSearchTestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run one image search against the Custom Image Search API."""
    from app.services.custom_image_search_service import (
        DEFAULT_BASE_URL,
        load_custom_image_search_config,
        normalize_base_url,
        test_custom_image_search_connection,
    )

    api_key = (body.api_key or "").strip() or None
    requested_base = (body.base_url or "").strip() or None
    cfg = load_custom_image_search_config(db, current_user.id)

    if not api_key:
        if not cfg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No Custom Image Search API key to test. Paste a key or save one first.",
            )
        api_key = cfg.api_key

    if requested_base:
        try:
            base_url = normalize_base_url(requested_base)
        except ValueError as exc:
            return LlmTestResponse(ok=False, message=str(exc))
        if not (body.api_key or "").strip() and cfg and base_url != cfg.base_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paste the API key when testing a different Custom Image Search URL.",
            )
    else:
        base_url = cfg.base_url if cfg else DEFAULT_BASE_URL

    ok, message = await test_custom_image_search_connection(
        api_key=api_key,
        base_url=base_url,
        query=(body.query or "technology").strip() or "technology",
    )
    return LlmTestResponse(ok=ok, message=message)
