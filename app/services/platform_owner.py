"""Platform owner helpers — only owner may use .env API keys."""

from __future__ import annotations

from typing import Optional, Union

from app.config import settings
from app.models.user import User


def is_platform_owner(user_or_email: Optional[Union[User, str]]) -> bool:
    """Return True if the user email matches PLATFORM_OWNER_EMAIL (case-insensitive)."""
    owner = (settings.platform_owner_email or "").strip().lower()
    if not owner:
        return False
    if user_or_email is None:
        return False
    if isinstance(user_or_email, User):
        email = (user_or_email.email or "").strip().lower()
    else:
        email = str(user_or_email).strip().lower()
    return bool(email) and email == owner
