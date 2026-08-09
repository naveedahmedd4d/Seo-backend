"""Per-user API keys and model preference resolution (owner-only .env fallback)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Union

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.models.user_llm_settings import UserLlmSettings
from app.schemas.settings_llm import LlmModelOption, LlmSettingsResponse
from app.services.platform_owner import is_platform_owner
from app.services.wordpress_service import decrypt_api_key, encrypt_api_key

Provider = Literal["openrouter", "groq"]
PreferredProvider = Literal["default", "openrouter", "groq"]
ApiKeyProvider = Literal[
    "openrouter",
    "groq",
    "trending_fallback_groq",
    "blog_fallback_groq",
    "serpapi",
    "tavily",
    "mangools",
    "apify",
]

DEFAULT_GROQ_BLOG_MODEL = "llama-3.3-70b-versatile"

_PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "groq": "Groq",
    "trending_fallback_groq": "Trending fallback Groq",
    "blog_fallback_groq": "Blog fallback Groq",
    "serpapi": "SerpAPI",
    "tavily": "Tavily",
    "mangools": "Mangools",
    "apify": "Apify",
}

_ROW_ATTR = {
    "openrouter": "openrouter_api_key",
    "groq": "groq_api_key",
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


def get_model_allowlist() -> List[LlmModelOption]:
    openrouter_default = getattr(settings, "blog_openrouter_model", None) or "openai/gpt-4o-mini"
    models: List[LlmModelOption] = [
        LlmModelOption(id="default", label="Default (platform)", provider="default"),
        LlmModelOption(
            id=openrouter_default,
            label=f"OpenRouter · {openrouter_default}",
            provider="openrouter",
        ),
        LlmModelOption(
            id="openai/gpt-4o-mini",
            label="OpenRouter · GPT-4o Mini",
            provider="openrouter",
        ),
        LlmModelOption(
            id="anthropic/claude-sonnet-4",
            label="OpenRouter · Claude Sonnet 4",
            provider="openrouter",
        ),
        LlmModelOption(
            id=DEFAULT_GROQ_BLOG_MODEL,
            label="Groq · Llama 3.3 70B",
            provider="groq",
        ),
        LlmModelOption(
            id="llama-3.1-8b-instant",
            label="Groq · Llama 3.1 8B Instant",
            provider="groq",
        ),
        LlmModelOption(
            id="meta-llama/llama-4-scout-17b-16e-instruct",
            label="Groq · Llama 4 Scout",
            provider="groq",
        ),
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
        "openrouter": settings.openrouter_api_key,
        "groq": settings.groq_api_key,
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
        return row
    row = UserLlmSettings(user_id=user_id, preferred_provider="default")
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
        openrouter_configured=bool(row.openrouter_api_key),
        openrouter_hint=_mask_hint(row.openrouter_api_key),
        openrouter_platform_available=platform_flag("openrouter"),
        groq_configured=bool(row.groq_api_key),
        groq_hint=_mask_hint(row.groq_api_key),
        groq_platform_available=platform_flag("groq"),
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
        updated_at=row.updated_at,
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
    openrouter_api_key: Optional[str] = None,
    groq_api_key: Optional[str] = None,
    trending_fallback_groq_api_key: Optional[str] = None,
    blog_fallback_groq_api_key: Optional[str] = None,
    serpapi_api_key: Optional[str] = None,
    tavily_api_key: Optional[str] = None,
    mangools_api_token: Optional[str] = None,
    apify_api_key: Optional[str] = None,
    clear_openrouter_api_key: bool = False,
    clear_groq_api_key: bool = False,
    clear_trending_fallback_groq_api_key: bool = False,
    clear_blog_fallback_groq_api_key: bool = False,
    clear_serpapi_api_key: bool = False,
    clear_tavily_api_key: bool = False,
    clear_mangools_api_token: bool = False,
    clear_apify_api_key: bool = False,
    preferred_provider: Optional[str] = None,
    preferred_model: Optional[str] = None,
) -> UserLlmSettings:
    row = get_or_create_settings(db, user_id)

    _apply_key_field(
        row, "openrouter_api_key", value=openrouter_api_key, clear=clear_openrouter_api_key
    )
    _apply_key_field(row, "groq_api_key", value=groq_api_key, clear=clear_groq_api_key)
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
    _apply_key_field(
        row, "mangools_api_token", value=mangools_api_token, clear=clear_mangools_api_token
    )
    _apply_key_field(row, "apify_api_key", value=apify_api_key, clear=clear_apify_api_key)

    if preferred_provider is not None:
        if preferred_provider not in ("default", "openrouter", "groq"):
            raise ValueError("preferred_provider must be default, openrouter, or groq")
        row.preferred_provider = preferred_provider

    if preferred_model is not None:
        normalized = preferred_model.strip() if preferred_model else ""
        if not normalized or normalized == "default":
            row.preferred_model = None
        else:
            if normalized not in _allowlist_ids():
                raise ValueError(f"Model not allowed: {normalized}")
            row.preferred_model = normalized

    db.commit()
    db.refresh(row)
    return row


def resolve_api_key(
    db: Session,
    user: Union[User, int],
    provider: ApiKeyProvider,
) -> str:
    """Resolve an API key: user saved key first, then .env only for platform owner."""
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
    """Primary Groq + optional blog/trending fallbacks (user first, owner .env)."""
    u = _load_user(db, user)
    keys: List[str] = []
    for provider in ("groq", "blog_fallback_groq", "trending_fallback_groq"):
        key = resolve_api_key_optional(db, u, provider)  # type: ignore[arg-type]
        if key and key not in keys:
            keys.append(key)
    return keys


def _platform_default_model(provider: Provider) -> str:
    if provider == "openrouter":
        return getattr(settings, "blog_openrouter_model", None) or "openai/gpt-4o-mini"
    return DEFAULT_GROQ_BLOG_MODEL


def _resolve_model(
    provider: Provider,
    preferred_model: Optional[str],
    model_override: Optional[str],
) -> str:
    allow = _allowlist_ids()
    if model_override:
        ov = model_override.strip()
        if ov and ov != "default":
            if ov not in allow:
                raise ValueError(f"Model not allowed: {ov}")
            return ov
    if preferred_model and preferred_model.strip() and preferred_model.strip() != "default":
        pm = preferred_model.strip()
        if pm in allow:
            return pm
    return _platform_default_model(provider)


def resolve_llm_credentials(
    db: Session,
    user_id: int,
    model_override: Optional[str] = None,
) -> LlmCredentials:
    """Resolve LLM provider/key/model. Platform .env only for platform owner."""
    user = _load_user(db, user_id)
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user.id).first()
    preferred: PreferredProvider = "default"
    preferred_model: Optional[str] = None
    user_or_key: Optional[str] = None
    user_groq_key: Optional[str] = None

    if row:
        preferred = (row.preferred_provider or "default")  # type: ignore[assignment]
        preferred_model = row.preferred_model
        user_or_key = _decrypt_user_key(row.openrouter_api_key)
        user_groq_key = _decrypt_user_key(row.groq_api_key)

    owner = is_platform_owner(user)
    platform_or = _platform_env_key("openrouter") if owner else None
    platform_groq = _platform_env_key("groq") if owner else None

    def build(provider: Provider, api_key: str, source: Literal["user", "platform"]) -> LlmCredentials:
        model = _resolve_model(provider, preferred_model, model_override)
        return LlmCredentials(provider=provider, api_key=api_key, model=model, source=source)

    if preferred == "openrouter":
        if user_or_key:
            return build("openrouter", user_or_key, "user")
        if platform_or:
            return build("openrouter", platform_or, "platform")
        if user_groq_key:
            return build("groq", user_groq_key, "user")
        if platform_groq:
            return build("groq", platform_groq, "platform")

    if preferred == "groq":
        if user_groq_key:
            return build("groq", user_groq_key, "user")
        if platform_groq:
            return build("groq", platform_groq, "platform")
        if user_or_key:
            return build("openrouter", user_or_key, "user")
        if platform_or:
            return build("openrouter", platform_or, "platform")

    if user_or_key:
        return build("openrouter", user_or_key, "user")
    if user_groq_key:
        return build("groq", user_groq_key, "user")
    if platform_or:
        return build("openrouter", platform_or, "platform")
    if platform_groq:
        return build("groq", platform_groq, "platform")

    raise ValueError(
        "Add your OpenRouter or Groq API key in Settings"
    )


def get_saved_provider_key(
    db: Session, user_id: int, provider: Literal["openrouter", "groq"]
) -> Optional[str]:
    row = db.query(UserLlmSettings).filter(UserLlmSettings.user_id == user_id).first()
    if not row:
        return None
    if provider == "openrouter":
        return _decrypt_user_key(row.openrouter_api_key)
    return _decrypt_user_key(row.groq_api_key)
