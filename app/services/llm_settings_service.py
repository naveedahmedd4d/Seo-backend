"""Per-user API keys, free LLM providers, and manual fallback lineup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Sequence, Union

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.models.user_llm_settings import UserLlmSettings
from app.schemas.settings_llm import LlmModelOption, LlmSettingsResponse
from app.services.platform_owner import is_platform_owner
from app.services.wordpress_service import decrypt_api_key, encrypt_api_key

Provider = Literal["gemini", "openrouter", "groq", "cloudflare", "mistral", "cohere", "nvidia"]
PreferredProvider = Literal[
    "default",
    "gemini",
    "openrouter",
    "groq",
    "cloudflare",
    "mistral",
    "cohere",
    "nvidia",
    "claude_routine",
]
ApiKeyProvider = Literal[
    "gemini",
    "openrouter",
    "groq",
    "cloudflare",
    "mistral",
    "cohere",
    "nvidia",
    "trending_fallback_groq",
    "blog_fallback_groq",
    "serpapi",
    "tavily",
    "mangools",
    "apify",
]

DEFAULT_FALLBACK_ORDER: List[Provider] = [
    "gemini",
    "openrouter",
    "groq",
    "cloudflare",
    "mistral",
    "cohere",
    "nvidia",
]

DEFAULT_GROQ_BLOG_MODEL = "openai/gpt-oss-120b"
DEFAULT_GEMINI_BLOG_MODEL = "gemini-3.8-flash"
DEFAULT_OPENROUTER_FREE_MODEL = "openrouter/free"
DEFAULT_CLOUDFLARE_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
DEFAULT_MISTRAL_MODEL = "mistral-small-latest"
DEFAULT_COHERE_MODEL = "command-r-plus-08-2024"
DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

GROQ_MODEL_MIGRATIONS = {
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "meta-llama/llama-4-scout-17b-16e-instruct": "openai/gpt-oss-20b",
    "meta-llama/llama-4-maverick-17b-128e-instruct": "openai/gpt-oss-120b",
}

_PROVIDER_LABELS = {
    "gemini": "Gemini",
    "openrouter": "OpenRouter",
    "groq": "Groq",
    "cloudflare": "Cloudflare Workers AI",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "nvidia": "NVIDIA NIM",
    "trending_fallback_groq": "Trending fallback Groq",
    "blog_fallback_groq": "Blog fallback Groq",
    "serpapi": "SerpAPI",
    "tavily": "Tavily",
    "mangools": "Mangools",
    "apify": "Apify",
}

_ROW_ATTR = {
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
    "groq": "groq_api_key",
    "cloudflare": "cloudflare_api_token",
    "mistral": "mistral_api_key",
    "cohere": "cohere_api_key",
    "nvidia": "nvidia_api_key",
    "trending_fallback_groq": "trending_fallback_groq_api_key",
    "blog_fallback_groq": "blog_fallback_groq_api_key",
    "serpapi": "serpapi_api_key",
    "tavily": "tavily_api_key",
    "mangools": "mangools_api_token",
    "apify": "apify_api_key",
}


@dataclass(frozen=True)
class LlmCredentials:
    provider: Provider
    api_key: str
    model: str
    source: Literal["user", "platform"]
    account_id: Optional[str] = None  # Cloudflare account id
    user_id: Optional[int] = None
    try_claude_routine: bool = False


def normalize_fallback_order(raw: Optional[Sequence[str]]) -> List[Provider]:
    allowed = set(DEFAULT_FALLBACK_ORDER)
    ordered: List[Provider] = []
    if raw:
        for item in raw:
            provider = str(item).strip().lower()
            if provider in allowed and provider not in ordered:
                ordered.append(provider)  # type: ignore[arg-type]
    for provider in DEFAULT_FALLBACK_ORDER:
        if provider not in ordered:
            ordered.append(provider)
    return ordered


def get_model_allowlist() -> List[LlmModelOption]:
    openrouter_default = (
        getattr(settings, "blog_openrouter_model", None) or DEFAULT_OPENROUTER_FREE_MODEL
    )
    gemini_default = getattr(settings, "blog_gemini_model", None) or DEFAULT_GEMINI_BLOG_MODEL
    cf_default = getattr(settings, "blog_cloudflare_model", None) or DEFAULT_CLOUDFLARE_MODEL
    mistral_default = getattr(settings, "blog_mistral_model", None) or DEFAULT_MISTRAL_MODEL
    cohere_default = getattr(settings, "blog_cohere_model", None) or DEFAULT_COHERE_MODEL
    nvidia_default = getattr(settings, "blog_nvidia_model", None) or DEFAULT_NVIDIA_MODEL

    models: List[LlmModelOption] = [
        LlmModelOption(id="default", label="Default (follow fallback lineup)", provider="default", free=True),
        # Gemini
        LlmModelOption(id=gemini_default, label=f"Gemini · {gemini_default} (free)", provider="gemini", free=True),
        LlmModelOption(id="gemini-3.8-flash", label="Gemini · 3.8 Flash (free)", provider="gemini", free=True),
        LlmModelOption(id="gemini-3.5-flash-lite", label="Gemini · 3.5 Flash-Lite (free)", provider="gemini", free=True),
        LlmModelOption(id="gemini-2.5-flash-lite", label="Gemini · 2.5 Flash-Lite (free)", provider="gemini", free=True),
        LlmModelOption(id="gemini-2.0-flash", label="Gemini · 2.0 Flash (free)", provider="gemini", free=True),
        LlmModelOption(id="gemini-2.0-flash-lite", label="Gemini · 2.0 Flash-Lite (free)", provider="gemini", free=True),
        LlmModelOption(id="gemini-1.5-flash", label="Gemini · 1.5 Flash (free)", provider="gemini", free=True),
        # OpenRouter free
        LlmModelOption(
            id=openrouter_default,
            label=f"OpenRouter · {openrouter_default}",
            provider="openrouter",
            free=openrouter_default.endswith(":free") or openrouter_default == "openrouter/free",
        ),
        LlmModelOption(id="openrouter/free", label="OpenRouter · Free Models Router", provider="openrouter", free=True),
        LlmModelOption(id="google/gemma-4-31b-it:free", label="OpenRouter · Gemma 4 31B (free)", provider="openrouter", free=True),
        LlmModelOption(id="google/gemma-4-26b-a4b-it:free", label="OpenRouter · Gemma 4 26B A4B (free)", provider="openrouter", free=True),
        LlmModelOption(id="nvidia/nemotron-3.5-lightning:free", label="OpenRouter · Nemotron 3.5 Lightning (free)", provider="openrouter", free=True),
        LlmModelOption(id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", label="OpenRouter · Nemotron 3 Nano Omni (free)", provider="openrouter", free=True),
        LlmModelOption(id="nvidia/nemotron-3-ultra-550b-a55b:free", label="OpenRouter · Nemotron 3 Ultra (free)", provider="openrouter", free=True),
        LlmModelOption(id="xingchen-agi/xing4.0-29b-a4b:free", label="OpenRouter · Xing4.0 29B (free)", provider="openrouter", free=True),
        LlmModelOption(id="dots-studio/dots-3-note-preview:free", label="OpenRouter · Dots3-Note (free)", provider="openrouter", free=True),
        LlmModelOption(id="cohere/north-mini-code:free", label="OpenRouter · North Mini Code (free)", provider="openrouter", free=True),
        LlmModelOption(id="liquid/lfm-2.5-2.6b:free", label="OpenRouter · LFM2.5 (free)", provider="openrouter", free=True),
        LlmModelOption(id="recursive/flint-v1.0:free", label="OpenRouter · Flint (free)", provider="openrouter", free=True),
        LlmModelOption(id="openai/gpt-4o-mini", label="OpenRouter · GPT-4o Mini", provider="openrouter", free=False),
        LlmModelOption(id="google/gemini-2.5-flash", label="OpenRouter · Gemini 2.5 Flash", provider="openrouter", free=False),
        # Groq
        LlmModelOption(id="openai/gpt-oss-20b", label="Groq · GPT-OSS 20B (free tier)", provider="groq", free=True),
        LlmModelOption(id=DEFAULT_GROQ_BLOG_MODEL, label="Groq · GPT-OSS 120B (free tier)", provider="groq", free=True),
        LlmModelOption(id="qwen/qwen3-32b", label="Groq · Qwen3 32B (free tier)", provider="groq", free=True),
        # Cloudflare Workers AI
        LlmModelOption(id=cf_default, label=f"Cloudflare · {cf_default} (free)", provider="cloudflare", free=True),
        LlmModelOption(id="@cf/meta/llama-3.3-70b-instruct-fp8-fast", label="Cloudflare · Llama 3.3 70B (free)", provider="cloudflare", free=True),
        LlmModelOption(id="@cf/qwen/qwen3-30b-a3b-fp8", label="Cloudflare · Qwen3 30B (free)", provider="cloudflare", free=True),
        LlmModelOption(id="@cf/google/gemma-3-12b-it", label="Cloudflare · Gemma 3 12B (free)", provider="cloudflare", free=True),
        LlmModelOption(id="@cf/openai/gpt-oss-20b", label="Cloudflare · GPT-OSS 20B (free)", provider="cloudflare", free=True),
        # Mistral
        LlmModelOption(id=mistral_default, label=f"Mistral · {mistral_default} (free mode)", provider="mistral", free=True),
        LlmModelOption(id="mistral-small-latest", label="Mistral · Small (free mode)", provider="mistral", free=True),
        LlmModelOption(id="mistral-nemo", label="Mistral · Nemo (free mode)", provider="mistral", free=True),
        LlmModelOption(id="open-mistral-7b", label="Mistral · Open 7B (free mode)", provider="mistral", free=True),
        # Cohere
        LlmModelOption(id=cohere_default, label=f"Cohere · {cohere_default} (trial)", provider="cohere", free=True),
        LlmModelOption(id="command-r-08-2024", label="Cohere · Command R (trial)", provider="cohere", free=True),
        # NVIDIA
        LlmModelOption(id=nvidia_default, label=f"NVIDIA · {nvidia_default} (dev free)", provider="nvidia", free=True),
        LlmModelOption(id="nvidia/nemotron-3.5-lightning-30b-a3b", label="NVIDIA · Nemotron 3.5 Lightning 30B (dev free)", provider="nvidia", free=True),
        LlmModelOption(id="nvidia/nemotron-nano-3-30b-a3b", label="NVIDIA · Nemotron Nano 3 30B (dev free)", provider="nvidia", free=True),
        LlmModelOption(id="google/gemma-4-31b-it", label="NVIDIA · Gemma 4 31B (dev free)", provider="nvidia", free=True),
        LlmModelOption(id="google/gemma-3-12b-it", label="NVIDIA · Gemma 3 12B (dev free)", provider="nvidia", free=True),
        LlmModelOption(id="mistralai/mistral-7b-instruct-v0.3", label="NVIDIA · Mistral 7B Instruct (dev free)", provider="nvidia", free=True),
    ]
    seen = set()
    unique: List[LlmModelOption] = []
    for m in models:
        if m.id in seen:
            continue
        seen.add(m.id)
        unique.append(m)
    return unique


def _allowlist_ids() -> set[str]:
    return {m.id for m in get_model_allowlist()}


def _mask_hint(encrypted_key: Optional[str]) -> Optional[str]:
    if not encrypted_key:
        return None
    try:
        raw = decrypt_api_key(encrypted_key)
    except Exception:
        return None
    if len(raw) <= 4:
        return raw
    return raw[-4:]


def _decrypt_user_key(encrypted: Optional[str]) -> Optional[str]:
    if not encrypted:
        return None
    try:
        return decrypt_api_key(encrypted)
    except Exception:
        return None


def _platform_env_key(provider: ApiKeyProvider) -> Optional[str]:
    mapping = {
        "gemini": getattr(settings, "gemini_api_key", None),
        "openrouter": settings.openrouter_api_key,
        "groq": settings.groq_api_key,
        "cloudflare": getattr(settings, "cloudflare_api_token", None),
        "mistral": getattr(settings, "mistral_api_key", None),
        "cohere": getattr(settings, "cohere_api_key", None),
        "nvidia": getattr(settings, "nvidia_api_key", None),
        "trending_fallback_groq": settings.trending_fallback_groq_api_key,
        "blog_fallback_groq": settings.blog_fallback_groq_api_key
        or settings.trending_fallback_groq_api_key,
        "serpapi": settings.serpapi_key,
        "tavily": settings.tavily_api_key,
        "mangools": settings.mangools_api_token,
        "apify": settings.apify_api_key,
    }
    val = mapping.get(provider)
    if val and str(val).strip():
        return str(val).strip()
    return None


def get_or_create_settings(db: Session, user_id: int) -> UserLlmSettings:
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user_id).first()
    if row:
        if not row.fallback_order:
            row.fallback_order = list(DEFAULT_FALLBACK_ORDER)
            db.commit()
            db.refresh(row)
        return row
    row = UserLlmSettings(
        user_id=user_id,
        preferred_provider="default",
        fallback_order=list(DEFAULT_FALLBACK_ORDER),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _load_user(db: Session, user: Union[User, int]) -> User:
    if isinstance(user, User):
        return user
    u = db.query(User).filter(User.id == user).first()
    if not u:
        raise ValueError("User not found")
    return u


def to_public_response(
    row: UserLlmSettings,
    *,
    user: Optional[User] = None,
) -> LlmSettingsResponse:
    owner = is_platform_owner(user) if user is not None else False

    def platform_flag(provider: ApiKeyProvider) -> bool:
        return bool(owner and _platform_env_key(provider))

    return LlmSettingsResponse(
        is_platform_owner=owner,
        gemini_configured=bool(getattr(row, "gemini_api_key", None)),
        gemini_hint=_mask_hint(getattr(row, "gemini_api_key", None)),
        gemini_platform_available=platform_flag("gemini"),
        openrouter_configured=bool(row.openrouter_api_key),
        openrouter_hint=_mask_hint(row.openrouter_api_key),
        openrouter_platform_available=platform_flag("openrouter"),
        groq_configured=bool(row.groq_api_key),
        groq_hint=_mask_hint(row.groq_api_key),
        groq_platform_available=platform_flag("groq"),
        cloudflare_configured=bool(getattr(row, "cloudflare_api_token", None)),
        cloudflare_hint=_mask_hint(getattr(row, "cloudflare_api_token", None)),
        cloudflare_platform_available=platform_flag("cloudflare"),
        cloudflare_account_id=getattr(row, "cloudflare_account_id", None) or None,
        mistral_configured=bool(getattr(row, "mistral_api_key", None)),
        mistral_hint=_mask_hint(getattr(row, "mistral_api_key", None)),
        mistral_platform_available=platform_flag("mistral"),
        cohere_configured=bool(getattr(row, "cohere_api_key", None)),
        cohere_hint=_mask_hint(getattr(row, "cohere_api_key", None)),
        cohere_platform_available=platform_flag("cohere"),
        nvidia_configured=bool(getattr(row, "nvidia_api_key", None)),
        nvidia_hint=_mask_hint(getattr(row, "nvidia_api_key", None)),
        nvidia_platform_available=platform_flag("nvidia"),
        claude_routine_url=(getattr(row, "claude_routine_url", None) or None),
        claude_routine_configured=_is_claude_routine_row_ready(row),
        claude_routine_token_configured=bool(getattr(row, "claude_routine_token", None)),
        claude_routine_token_hint=_mask_hint(getattr(row, "claude_routine_token", None)),
        claude_routine_callback_secret_configured=bool(
            getattr(row, "claude_routine_callback_secret", None)
        ),
        claude_routine_callback_secret_hint=_mask_hint(
            getattr(row, "claude_routine_callback_secret", None)
        ),
        claude_routine_webhook_path="/api/webhooks/claude-routine",
        trending_fallback_groq_configured=bool(row.trending_fallback_groq_api_key),
        trending_fallback_groq_hint=_mask_hint(row.trending_fallback_groq_api_key),
        trending_fallback_groq_platform_available=platform_flag("trending_fallback_groq"),
        blog_fallback_groq_configured=bool(row.blog_fallback_groq_api_key),
        blog_fallback_groq_hint=_mask_hint(row.blog_fallback_groq_api_key),
        blog_fallback_groq_platform_available=platform_flag("blog_fallback_groq"),
        serpapi_configured=bool(row.serpapi_api_key),
        serpapi_hint=_mask_hint(row.serpapi_api_key),
        serpapi_platform_available=platform_flag("serpapi"),
        tavily_configured=bool(row.tavily_api_key),
        tavily_hint=_mask_hint(row.tavily_api_key),
        tavily_platform_available=platform_flag("tavily"),
        mangools_configured=bool(row.mangools_api_token),
        mangools_hint=_mask_hint(row.mangools_api_token),
        mangools_platform_available=platform_flag("mangools"),
        apify_configured=bool(row.apify_api_key),
        apify_hint=_mask_hint(row.apify_api_key),
        apify_platform_available=platform_flag("apify"),
        preferred_provider=row.preferred_provider or "default",  # type: ignore[arg-type]
        preferred_model=row.preferred_model,
        fallback_order=normalize_fallback_order(row.fallback_order),  # type: ignore[arg-type]
        updated_at=row.updated_at,
    )


def _is_claude_routine_row_ready(row: UserLlmSettings) -> bool:
    from app.services.claude_routine_service import is_valid_fire_url

    return bool(
        is_valid_fire_url(getattr(row, "claude_routine_url", None))
        and getattr(row, "claude_routine_token", None)
        and getattr(row, "claude_routine_callback_secret", None)
    )


def _apply_key_field(
    row: UserLlmSettings,
    attr: str,
    *,
    value: Optional[str],
    clear: bool,
) -> None:
    if clear:
        setattr(row, attr, None)
    elif value is not None and value.strip():
        setattr(row, attr, encrypt_api_key(value.strip()))


def upsert_settings(
    db: Session,
    user_id: int,
    *,
    gemini_api_key: Optional[str] = None,
    openrouter_api_key: Optional[str] = None,
    groq_api_key: Optional[str] = None,
    cloudflare_api_token: Optional[str] = None,
    cloudflare_account_id: Optional[str] = None,
    mistral_api_key: Optional[str] = None,
    cohere_api_key: Optional[str] = None,
    nvidia_api_key: Optional[str] = None,
    claude_routine_url: Optional[str] = None,
    claude_routine_token: Optional[str] = None,
    claude_routine_callback_secret: Optional[str] = None,
    trending_fallback_groq_api_key: Optional[str] = None,
    blog_fallback_groq_api_key: Optional[str] = None,
    serpapi_api_key: Optional[str] = None,
    tavily_api_key: Optional[str] = None,
    mangools_api_token: Optional[str] = None,
    apify_api_key: Optional[str] = None,
    clear_gemini_api_key: bool = False,
    clear_openrouter_api_key: bool = False,
    clear_groq_api_key: bool = False,
    clear_cloudflare_api_token: bool = False,
    clear_mistral_api_key: bool = False,
    clear_cohere_api_key: bool = False,
    clear_nvidia_api_key: bool = False,
    clear_claude_routine: bool = False,
    clear_trending_fallback_groq_api_key: bool = False,
    clear_blog_fallback_groq_api_key: bool = False,
    clear_serpapi_api_key: bool = False,
    clear_tavily_api_key: bool = False,
    clear_mangools_api_token: bool = False,
    clear_apify_api_key: bool = False,
    preferred_provider: Optional[str] = None,
    preferred_model: Optional[str] = None,
    fallback_order: Optional[Sequence[str]] = None,
) -> UserLlmSettings:
    row = get_or_create_settings(db, user_id)

    _apply_key_field(row, "gemini_api_key", value=gemini_api_key, clear=clear_gemini_api_key)
    _apply_key_field(row, "openrouter_api_key", value=openrouter_api_key, clear=clear_openrouter_api_key)
    _apply_key_field(row, "groq_api_key", value=groq_api_key, clear=clear_groq_api_key)
    _apply_key_field(
        row, "cloudflare_api_token", value=cloudflare_api_token, clear=clear_cloudflare_api_token
    )
    if cloudflare_account_id is not None:
        cleaned = cloudflare_account_id.strip()
        row.cloudflare_account_id = cleaned or None
    _apply_key_field(row, "mistral_api_key", value=mistral_api_key, clear=clear_mistral_api_key)
    _apply_key_field(row, "cohere_api_key", value=cohere_api_key, clear=clear_cohere_api_key)
    _apply_key_field(row, "nvidia_api_key", value=nvidia_api_key, clear=clear_nvidia_api_key)
    if clear_claude_routine:
        row.claude_routine_url = None
        row.claude_routine_token = None
        row.claude_routine_callback_secret = None
    else:
        if claude_routine_url is not None:
            cleaned_url = claude_routine_url.strip()
            if cleaned_url:
                from app.services.claude_routine_service import validate_fire_url

                row.claude_routine_url = validate_fire_url(cleaned_url)
            else:
                row.claude_routine_url = None
        _apply_key_field(
            row, "claude_routine_token", value=claude_routine_token, clear=False
        )
        _apply_key_field(
            row,
            "claude_routine_callback_secret",
            value=claude_routine_callback_secret,
            clear=False,
        )
    _apply_key_field(
        row,
        "trending_fallback_groq_api_key",
        value=trending_fallback_groq_api_key,
        clear=clear_trending_fallback_groq_api_key,
    )
    _apply_key_field(
        row,
        "blog_fallback_groq_api_key",
        value=blog_fallback_groq_api_key,
        clear=clear_blog_fallback_groq_api_key,
    )
    _apply_key_field(row, "serpapi_api_key", value=serpapi_api_key, clear=clear_serpapi_api_key)
    _apply_key_field(row, "tavily_api_key", value=tavily_api_key, clear=clear_tavily_api_key)
    _apply_key_field(row, "mangools_api_token", value=mangools_api_token, clear=clear_mangools_api_token)
    _apply_key_field(row, "apify_api_key", value=apify_api_key, clear=clear_apify_api_key)

    if preferred_provider is not None:
        allowed = {"default", "claude_routine", *DEFAULT_FALLBACK_ORDER}
        if preferred_provider not in allowed:
            raise ValueError(
                "preferred_provider must be default, claude_routine, gemini, openrouter, "
                "groq, cloudflare, mistral, cohere, or nvidia"
            )
        row.preferred_provider = preferred_provider

    if preferred_model is not None:
        normalized = preferred_model.strip() if preferred_model else ""
        if not normalized or normalized == "default":
            row.preferred_model = None
        else:
            if normalized not in _allowlist_ids():
                raise ValueError(f"Model not allowed: {normalized}")
            row.preferred_model = normalized

    if fallback_order is not None:
        row.fallback_order = normalize_fallback_order(fallback_order)

    db.commit()
    db.refresh(row)
    return row


def resolve_api_key(
    db: Session,
    user: Union[User, int],
    provider: ApiKeyProvider,
) -> str:
    u = _load_user(db, user)
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == u.id).first()
    attr = _ROW_ATTR[provider]
    user_key = _decrypt_user_key(getattr(row, attr, None) if row else None)
    if user_key:
        return user_key
    if is_platform_owner(u):
        platform = _platform_env_key(provider)
        if platform:
            return platform
    label = _PROVIDER_LABELS.get(provider, provider)
    raise ValueError(f"Add your {label} API key in Settings")


def resolve_api_key_optional(
    db: Session,
    user: Union[User, int],
    provider: ApiKeyProvider,
) -> Optional[str]:
    try:
        return resolve_api_key(db, user, provider)
    except ValueError:
        return None


def get_blog_groq_key_chain(db: Session, user: Union[User, int]) -> List[str]:
    u = _load_user(db, user)
    keys: List[str] = []
    for provider in ("groq", "blog_fallback_groq", "trending_fallback_groq"):
        key = resolve_api_key_optional(db, u, provider)  # type: ignore[arg-type]
        if key and key not in keys:
            keys.append(key)
    return keys


def _platform_default_model(provider: Provider) -> str:
    if provider == "gemini":
        return getattr(settings, "blog_gemini_model", None) or DEFAULT_GEMINI_BLOG_MODEL
    if provider == "openrouter":
        return getattr(settings, "blog_openrouter_model", None) or DEFAULT_OPENROUTER_FREE_MODEL
    if provider == "cloudflare":
        return getattr(settings, "blog_cloudflare_model", None) or DEFAULT_CLOUDFLARE_MODEL
    if provider == "mistral":
        return getattr(settings, "blog_mistral_model", None) or DEFAULT_MISTRAL_MODEL
    if provider == "cohere":
        return getattr(settings, "blog_cohere_model", None) or DEFAULT_COHERE_MODEL
    if provider == "nvidia":
        return getattr(settings, "blog_nvidia_model", None) or DEFAULT_NVIDIA_MODEL
    return DEFAULT_GROQ_BLOG_MODEL


def _migrate_groq_model_id(model_id: str) -> str:
    return GROQ_MODEL_MIGRATIONS.get(model_id, model_id)


def _model_belongs_to_provider(model_id: str, provider: Provider) -> bool:
    for option in get_model_allowlist():
        if option.id == model_id:
            return option.provider == provider or option.provider == "default"
    return False


def _resolve_model(
    provider: Provider,
    preferred_model: Optional[str],
    model_override: Optional[str],
) -> str:
    allow = _allowlist_ids()
    if model_override:
        ov = _migrate_groq_model_id(model_override.strip())
        if ov and ov != "default":
            if ov not in allow:
                raise ValueError(f"Model not allowed: {ov}")
            if _model_belongs_to_provider(ov, provider):
                return ov
    if preferred_model and preferred_model.strip() and preferred_model.strip() != "default":
        pm = _migrate_groq_model_id(preferred_model.strip())
        if pm in allow and _model_belongs_to_provider(pm, provider):
            return pm
    return _platform_default_model(provider)


def _cloudflare_account_id(row: Optional[UserLlmSettings], owner: bool) -> Optional[str]:
    if row and getattr(row, "cloudflare_account_id", None):
        return str(row.cloudflare_account_id).strip() or None
    if owner:
        platform = getattr(settings, "cloudflare_account_id", None)
        if platform and str(platform).strip():
            return str(platform).strip()
    return None


def resolve_llm_credentials(
    db: Session,
    user_id: int,
    model_override: Optional[str] = None,
) -> LlmCredentials:
    """Resolve LLM using preferred provider + manual fallback_order."""
    user = _load_user(db, user_id)
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user.id).first()
    preferred: PreferredProvider = "default"
    preferred_model: Optional[str] = None
    fallback = list(DEFAULT_FALLBACK_ORDER)
    try_claude_routine = False

    keys: dict[Provider, tuple[Optional[str], Optional[str]]] = {
        "gemini": (None, None),
        "openrouter": (None, None),
        "groq": (None, None),
        "cloudflare": (None, None),
        "mistral": (None, None),
        "cohere": (None, None),
        "nvidia": (None, None),
    }

    owner = is_platform_owner(user)
    if row:
        raw_preferred = (row.preferred_provider or "default")
        from app.services.claude_routine_service import is_routine_configured

        if raw_preferred == "claude_routine":
            try_claude_routine = is_routine_configured(db, user.id)
            preferred = "default"
        else:
            preferred = raw_preferred  # type: ignore[assignment]
        preferred_model = row.preferred_model
        fallback = normalize_fallback_order(row.fallback_order)
        keys = {
            "gemini": (_decrypt_user_key(getattr(row, "gemini_api_key", None)), _platform_env_key("gemini") if owner else None),
            "openrouter": (_decrypt_user_key(row.openrouter_api_key), _platform_env_key("openrouter") if owner else None),
            "groq": (_decrypt_user_key(row.groq_api_key), _platform_env_key("groq") if owner else None),
            "cloudflare": (
                _decrypt_user_key(getattr(row, "cloudflare_api_token", None)),
                _platform_env_key("cloudflare") if owner else None,
            ),
            "mistral": (_decrypt_user_key(getattr(row, "mistral_api_key", None)), _platform_env_key("mistral") if owner else None),
            "cohere": (_decrypt_user_key(getattr(row, "cohere_api_key", None)), _platform_env_key("cohere") if owner else None),
            "nvidia": (_decrypt_user_key(getattr(row, "nvidia_api_key", None)), _platform_env_key("nvidia") if owner else None),
        }
    else:
        keys = {
            p: (None, _platform_env_key(p) if owner else None)  # type: ignore[arg-type]
            for p in DEFAULT_FALLBACK_ORDER
        }

    order: List[Provider] = []
    if preferred != "default" and preferred in DEFAULT_FALLBACK_ORDER:
        order.append(preferred)  # type: ignore[arg-type]
    for provider in fallback:
        if provider not in order:
            order.append(provider)

    account_id = _cloudflare_account_id(row, owner)

    for provider in order:
        user_key, platform_key = keys[provider]
        api_key = user_key or platform_key
        if not api_key:
            continue
        if provider == "cloudflare" and not account_id:
            continue
        source: Literal["user", "platform"] = "user" if user_key else "platform"
        model = _resolve_model(provider, preferred_model, model_override)
        return LlmCredentials(
            provider=provider,
            api_key=api_key,
            model=model,
            source=source,
            account_id=account_id if provider == "cloudflare" else None,
            user_id=user.id,
            try_claude_routine=try_claude_routine,
        )

    if try_claude_routine:
        # Routine-only mode: still return a stub so callers can try the webhook path.
        # generate_content will fire the routine; if it fails, raise a clear error.
        return LlmCredentials(
            provider="gemini",
            api_key="",
            model=_platform_default_model("gemini"),
            source="user",
            user_id=user.id,
            try_claude_routine=True,
        )

    raise ValueError(
        "Add at least one LLM API key in Settings "
        "(Gemini, OpenRouter, Groq, Cloudflare, Mistral, Cohere, or NVIDIA)"
    )


def get_saved_provider_key(
    db: Session,
    user_id: int,
    provider: Provider,
) -> Optional[str]:
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user_id).first()
    if not row:
        return None
    attr = _ROW_ATTR.get(provider)
    if not attr:
        return None
    return _decrypt_user_key(getattr(row, attr, None))
