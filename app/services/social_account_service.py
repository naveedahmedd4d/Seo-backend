import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.social_account import SocialAccount
from app.services.wordpress_service import decrypt_api_key, encrypt_api_key


def _parse_metadata(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _dump_metadata(data: dict[str, Any]) -> str:
    return json.dumps(data)


def get_user_social_accounts(db: Session, user_id: int) -> list[SocialAccount]:
    return (
        db.query(SocialAccount)
        .filter(SocialAccount.user_id == user_id)
        .order_by(SocialAccount.platform.asc())
        .all()
    )


def get_user_social_account(
    db: Session, user_id: int, platform: str
) -> Optional[SocialAccount]:
    return (
        db.query(SocialAccount)
        .filter(SocialAccount.user_id == user_id, SocialAccount.platform == platform)
        .first()
    )


def get_social_account_by_id(
    db: Session, account_id: int, user_id: int
) -> Optional[SocialAccount]:
    return (
        db.query(SocialAccount)
        .filter(SocialAccount.id == account_id, SocialAccount.user_id == user_id)
        .first()
    )


def upsert_social_account_tokens(
    db: Session,
    user_id: int,
    platform: str,
    *,
    access_token: str,
    refresh_token: Optional[str],
    token_expires_at: Optional[datetime],
    account_label: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> SocialAccount:
    account = get_user_social_account(db, user_id, platform)
    encrypted_access = encrypt_api_key(access_token)
    encrypted_refresh = encrypt_api_key(refresh_token) if refresh_token else None

    if account:
        account.access_token = encrypted_access
        account.refresh_token = encrypted_refresh
        account.token_expires_at = token_expires_at
        if account_label:
            account.account_label = account_label
        if metadata is not None:
            account.account_metadata = _dump_metadata(metadata)
        account.updated_at = datetime.now(timezone.utc)
    else:
        account = SocialAccount(
            user_id=user_id,
            platform=platform,
            account_label=account_label,
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            token_expires_at=token_expires_at,
            account_metadata=_dump_metadata(metadata or {}),
        )
        db.add(account)

    db.commit()
    db.refresh(account)
    return account


def update_social_account_metadata(
    db: Session,
    account: SocialAccount,
    metadata: dict[str, Any],
) -> SocialAccount:
    account.account_metadata = _dump_metadata(metadata)
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(account)
    return account


def delete_social_account(db: Session, account: SocialAccount) -> None:
    db.delete(account)
    db.commit()


def get_decrypted_access_token(account: SocialAccount) -> str:
    if not account.access_token:
        raise ValueError("Social account has no access token")
    return decrypt_api_key(account.access_token)


def get_decrypted_refresh_token(account: SocialAccount) -> Optional[str]:
    if not account.refresh_token:
        return None
    return decrypt_api_key(account.refresh_token)


def get_account_metadata(account: SocialAccount) -> dict[str, Any]:
    return _parse_metadata(account.account_metadata)
