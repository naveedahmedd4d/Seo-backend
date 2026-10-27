import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.social_platform_app import SocialPlatformApp
from app.services.public_url import build_oauth_redirect_uri
from app.services.wordpress_service import decrypt_api_key, encrypt_api_key

_PLACEHOLDER_CLIENT_IDS = frozenset(
    {"your_app_id", "your-client-id", "changeme", "placeholder", "xxx", "test"}
)
_PLACEHOLDER_SECRETS = frozenset(
    {"your_app_secret", "your-client-secret", "your_app_secret_key", "changeme", "placeholder", "xxx", "test"}
)


@dataclass(frozen=True)
class OAuthAppCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass(frozen=True)
class MastodonAppCredentials(OAuthAppCredentials):
    instance_url: str


def get_platform_redirect_uri(platform: str, request: Optional["Request"] = None) -> str:
    return build_oauth_redirect_uri(platform, request)


def get_pinterest_redirect_uri(request: Optional["Request"] = None) -> str:
    return get_platform_redirect_uri("pinterest", request)


def get_x_redirect_uri(request: Optional["Request"] = None) -> str:
    return get_platform_redirect_uri("x", request)


def get_mastodon_redirect_uri(request: Optional["Request"] = None) -> str:
    return get_platform_redirect_uri("mastodon", request)


def get_blogger_redirect_uri(request: Optional["Request"] = None) -> str:
    return get_platform_redirect_uri("blogger", request)


def get_reddit_redirect_uri(request: Optional["Request"] = None) -> str:
    return get_platform_redirect_uri("reddit", request)


def get_linkedin_redirect_uri(request: Optional["Request"] = None) -> str:
    return get_platform_redirect_uri("linkedin", request)


def get_facebook_redirect_uri(request: Optional["Request"] = None) -> str:
    return get_platform_redirect_uri("facebook", request)


def get_instagram_redirect_uri(request: Optional["Request"] = None) -> str:
    return get_platform_redirect_uri("instagram", request)


def _is_placeholder(value: Optional[str], placeholders: frozenset[str]) -> bool:
    if not value or not value.strip():
        return True
    return value.strip().lower() in placeholders


def normalize_mastodon_instance(instance_url: str) -> str:
    raw = instance_url.strip().rstrip("/")
    if not raw:
        raise ValueError("Mastodon instance URL is required")
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if not parsed.netloc:
        raise ValueError("Enter a valid Mastodon instance URL (e.g. mastodon.social)")
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_oauth_app_credentials(client_id: str, client_secret: str) -> Optional[str]:
    cid = client_id.strip()
    if _is_placeholder(cid, _PLACEHOLDER_CLIENT_IDS):
        return "Enter your real OAuth client ID from the developer portal (not a placeholder)."
    if "@" in cid:
        return "Client ID is not your login email. Copy the OAuth client ID from the developer portal."
    if _is_placeholder(client_secret, _PLACEHOLDER_SECRETS):
        return "Enter your real OAuth client secret from the developer portal."
    if len(client_secret.strip()) < 8:
        return "OAuth client secret looks too short."
    return None


def validate_pinterest_app_credentials(client_id: str, client_secret: str) -> Optional[str]:
    return validate_oauth_app_credentials(client_id, client_secret)


def validate_x_app_credentials(client_id: str, client_secret: str) -> Optional[str]:
    return validate_oauth_app_credentials(client_id, client_secret)


def validate_mastodon_app_credentials(
    instance_url: str, client_id: str, client_secret: str
) -> Optional[str]:
    try:
        normalize_mastodon_instance(instance_url)
    except ValueError as exc:
        return str(exc)
    return validate_oauth_app_credentials(client_id, client_secret)


def _encrypt_mastodon_bundle(client_secret: str, instance_url: str) -> str:
    payload = json.dumps({"secret": client_secret.strip(), "instance_url": instance_url})
    return encrypt_api_key(payload)


def _decrypt_mastodon_bundle(encrypted: str) -> tuple[str, str]:
    data = json.loads(decrypt_api_key(encrypted))
    return str(data["secret"]), str(data["instance_url"])


def get_user_platform_app(
    db: Session, user_id: int, platform: str
) -> Optional[SocialPlatformApp]:
    return (
        db.query(SocialPlatformApp)
        .filter(SocialPlatformApp.user_id == user_id, SocialPlatformApp.platform == platform)
        .first()
    )


def upsert_user_platform_app(
    db: Session,
    user_id: int,
    platform: str,
    client_id: str,
    client_secret: str,
    *,
    instance_url: Optional[str] = None,
) -> SocialPlatformApp:
    validation_error = None
    if platform == "pinterest":
        validation_error = validate_pinterest_app_credentials(client_id, client_secret)
    elif platform == "x":
        validation_error = validate_x_app_credentials(client_id, client_secret)
    elif platform in {"blogger", "gsc"}:
        validation_error = validate_oauth_app_credentials(client_id, client_secret)
    elif platform in {"reddit", "facebook"}:
        validation_error = validate_oauth_app_credentials(client_id, client_secret)
    elif platform == "mastodon":
        validation_error = validate_mastodon_app_credentials(
            instance_url or "", client_id, client_secret
        )
    if validation_error:
        raise ValueError(validation_error)

    row = get_user_platform_app(db, user_id, platform)
    if platform == "mastodon":
        normalized = normalize_mastodon_instance(instance_url or "")
        encrypted_secret = _encrypt_mastodon_bundle(client_secret, normalized)
    else:
        encrypted_secret = encrypt_api_key(client_secret.strip())

    if row:
        row.client_id = client_id.strip()
        row.client_secret = encrypted_secret
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = SocialPlatformApp(
            user_id=user_id,
            platform=platform,
            client_id=client_id.strip(),
            client_secret=encrypted_secret,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_user_platform_app(db: Session, user_id: int, platform: str) -> None:
    row = get_user_platform_app(db, user_id, platform)
    if row:
        db.delete(row)
        db.commit()


def get_pinterest_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[OAuthAppCredentials]:
    row = get_user_platform_app(db, user_id, "pinterest")
    if not row:
        return None
    return OAuthAppCredentials(
        client_id=row.client_id,
        client_secret=decrypt_api_key(row.client_secret),
        redirect_uri=redirect_uri or get_pinterest_redirect_uri(),
    )


def get_x_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[OAuthAppCredentials]:
    row = get_user_platform_app(db, user_id, "x")
    if not row:
        return None
    return OAuthAppCredentials(
        client_id=row.client_id,
        client_secret=decrypt_api_key(row.client_secret),
        redirect_uri=redirect_uri or get_x_redirect_uri(),
    )


def get_mastodon_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[MastodonAppCredentials]:
    row = get_user_platform_app(db, user_id, "mastodon")
    if not row:
        return None
    secret, instance_url = _decrypt_mastodon_bundle(row.client_secret)
    return MastodonAppCredentials(
        client_id=row.client_id,
        client_secret=secret,
        redirect_uri=redirect_uri or get_mastodon_redirect_uri(),
        instance_url=instance_url,
    )


def get_blogger_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[OAuthAppCredentials]:
    row = get_user_platform_app(db, user_id, "blogger")
    if not row:
        return None
    return OAuthAppCredentials(
        client_id=row.client_id,
        client_secret=decrypt_api_key(row.client_secret),
        redirect_uri=redirect_uri or get_blogger_redirect_uri(),
    )


def gsc_platform_app_available() -> bool:
    """Whether a platform-wide (shared, app-owned) Google OAuth client is configured.

    When set, new users don't need to bring their own Google Cloud OAuth client —
    they just click Connect and consent. Per-user BYO credentials (if a user has
    saved their own) still take priority, so existing BYO users are unaffected.
    """
    from app.config import settings

    return bool(settings.google_client_id and settings.google_client_secret)


def get_gsc_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[OAuthAppCredentials]:
    from app.config import settings
    from app.services.gsc_oauth_service import get_gsc_redirect_uri

    resolved_redirect_uri = redirect_uri or get_gsc_redirect_uri()

    row = get_user_platform_app(db, user_id, "gsc")
    if row:
        return OAuthAppCredentials(
            client_id=row.client_id,
            client_secret=decrypt_api_key(row.client_secret),
            redirect_uri=resolved_redirect_uri,
        )

    if settings.google_client_id and settings.google_client_secret:
        return OAuthAppCredentials(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=resolved_redirect_uri,
        )

    return None


def get_reddit_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[OAuthAppCredentials]:
    row = get_user_platform_app(db, user_id, "reddit")
    if not row:
        return None
    return OAuthAppCredentials(
        client_id=row.client_id,
        client_secret=decrypt_api_key(row.client_secret),
        redirect_uri=redirect_uri or get_reddit_redirect_uri(),
    )


def get_linkedin_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[OAuthAppCredentials]:
    row = get_user_platform_app(db, user_id, "linkedin")
    if not row:
        return None
    return OAuthAppCredentials(
        client_id=row.client_id,
        client_secret=decrypt_api_key(row.client_secret),
        redirect_uri=redirect_uri or get_linkedin_redirect_uri(),
    )


def get_facebook_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[OAuthAppCredentials]:
    """Resolve Meta app credentials for Facebook Pages OAuth.

    Order: server env → the user's own Meta app → platform-owner shared app.
    """
    from app.config import settings

    resolved_redirect_uri = redirect_uri or get_facebook_redirect_uri()
    if settings.meta_app_id and settings.meta_app_secret:
        return OAuthAppCredentials(
            client_id=settings.meta_app_id.strip(),
            client_secret=settings.meta_app_secret.strip(),
            redirect_uri=resolved_redirect_uri,
        )

    user_row = get_user_platform_app(db, user_id, "facebook")
    if user_row:
        return OAuthAppCredentials(
            client_id=user_row.client_id,
            client_secret=decrypt_api_key(user_row.client_secret),
            redirect_uri=resolved_redirect_uri,
        )

    if not settings.platform_owner_email:
        return None

    # Admin-entered platform credentials are stored encrypted against the
    # designated owner account, then shared for server-side OAuth calls.
    from app.models.user import User

    owner = (
        db.query(User)
        .filter(func.lower(User.email) == settings.platform_owner_email.strip().casefold())
        .first()
    )
    if not owner:
        return None
    row = get_user_platform_app(db, owner.id, "facebook")
    if not row:
        return None
    return OAuthAppCredentials(
        client_id=row.client_id,
        client_secret=decrypt_api_key(row.client_secret),
        redirect_uri=resolved_redirect_uri,
    )


def get_instagram_app_credentials(
    db: Session,
    user_id: int,
    *,
    redirect_uri: Optional[str] = None,
) -> Optional[OAuthAppCredentials]:
    """Return the platform-owned Instagram Login app credentials."""
    from app.config import settings

    resolved_redirect_uri = redirect_uri or get_instagram_redirect_uri()
    app_id = settings.instagram_app_id or settings.meta_app_id
    app_secret = settings.instagram_app_secret or settings.meta_app_secret
    if app_id and app_secret:
        return OAuthAppCredentials(
            client_id=app_id.strip(),
            client_secret=app_secret.strip(),
            redirect_uri=resolved_redirect_uri,
        )

    if not settings.platform_owner_email:
        return None

    from app.models.user import User

    owner = (
        db.query(User)
        .filter(func.lower(User.email) == settings.platform_owner_email.strip().casefold())
        .first()
    )
    if not owner:
        return None
    row = get_user_platform_app(db, owner.id, "instagram")
    if not row:
        return None
    return OAuthAppCredentials(
        client_id=row.client_id,
        client_secret=decrypt_api_key(row.client_secret),
        redirect_uri=resolved_redirect_uri,
    )


def user_has_pinterest_app(db: Session, user_id: int) -> bool:
    return get_pinterest_app_credentials(db, user_id) is not None


def user_has_x_app(db: Session, user_id: int) -> bool:
    return get_x_app_credentials(db, user_id) is not None


def user_has_mastodon_app(db: Session, user_id: int) -> bool:
    return get_mastodon_app_credentials(db, user_id) is not None


def user_has_blogger_app(db: Session, user_id: int) -> bool:
    return get_blogger_app_credentials(db, user_id) is not None


def user_has_gsc_app(db: Session, user_id: int) -> bool:
    return get_gsc_app_credentials(db, user_id) is not None


def user_has_reddit_app(db: Session, user_id: int) -> bool:
    return get_reddit_app_credentials(db, user_id) is not None


def user_has_linkedin_app(db: Session, user_id: int) -> bool:
    return get_linkedin_app_credentials(db, user_id) is not None


def user_has_facebook_app(db: Session, user_id: int) -> bool:
    return get_facebook_app_credentials(db, user_id) is not None


def user_has_instagram_app(db: Session, user_id: int) -> bool:
    return get_instagram_app_credentials(db, user_id) is not None


# Platforms that store credentials only on social_accounts (no social_platform_apps row)
CREDENTIAL_ONLY_PLATFORMS = frozenset({"bluesky", "devto", "hashnode"})


# Backward-compatible alias used by pinterest_service
PinterestAppCredentials = OAuthAppCredentials
