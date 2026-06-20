from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash"""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        # Legacy or corrupted hash stored in the database.
        return False


def get_password_hash(password: str) -> str:
    """Hash a password"""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(seconds=settings.jwt_expiration)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)
    return encoded_jwt


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """Authenticate a user by email and password"""
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get a user by email"""
    return db.query(User).filter(User.email == email).first()


def create_user(
    db: Session,
    email: str,
    password: str,
    full_name: Optional[str] = None,
) -> User:
    """Create a new user"""
    hashed_password = get_password_hash(password)
    user = User(
        email=email,
        password_hash=hashed_password,
        full_name=full_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_oauth_state_token(
    user: User,
    platform: str,
    *,
    extra: Optional[dict] = None,
) -> str:
    """Short-lived JWT used as OAuth state parameter."""
    payload: dict = {
        "sub": user.email,
        "typ": "oauth_state",
        "oauth_platform": platform,
    }
    if extra:
        payload["oauth_extra"] = extra
    return create_access_token(
        payload,
        expires_delta=timedelta(minutes=10),
    )


def decode_oauth_state(state: str, platform: str) -> tuple[Optional[dict], Optional[dict]]:
    """Return (user_email_payload, extra) if state is valid for platform."""
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("typ") != "oauth_state":
            return None, None
        if payload.get("oauth_platform") != platform:
            return None, None
        email: str = payload.get("sub")
        if not email:
            return None, None
        extra = payload.get("oauth_extra")
        return {"email": email}, extra if isinstance(extra, dict) else None
    except JWTError:
        return None, None


def get_user_from_oauth_state(state: str, db: Session, platform: str) -> Optional[User]:
    user_payload, _ = decode_oauth_state(state, platform)
    if not user_payload:
        return None
    return get_user_by_email(db, user_payload["email"])


def get_current_user_from_token(token: str, db: Session) -> Optional[User]:
    """Get current user from JWT token"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        email: str = payload.get("sub")
        if email is None:
            return None
    except JWTError:
        return None
    user = get_user_by_email(db, email)
    return user
