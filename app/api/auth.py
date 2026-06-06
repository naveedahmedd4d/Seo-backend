from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import Token, UserRegister, UserResponse
from app.services.auth_service import (
    authenticate_user,
    create_access_token,
    create_user,
    get_current_user_from_token,
    get_user_by_email,
)

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

ACCESS_TOKEN_COOKIE = "access_token"


def _set_access_token_cookie(response: JSONResponse, access_token: str) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        samesite="lax",
        max_age=settings.jwt_expiration,
        path="/",
    )


def _resolve_token(
    request: Request,
    bearer_token: str | None,
    cookie_token: str | None,
) -> str | None:
    if bearer_token:
        return bearer_token
    if cookie_token:
        return cookie_token
    return request.cookies.get(ACCESS_TOKEN_COOKIE)


def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    access_token: str | None = Cookie(default=None, alias=ACCESS_TOKEN_COOKIE),
    db: Session = Depends(get_db),
) -> User:
    credentials_token = _resolve_token(request, token, access_token)
    if not credentials_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_current_user_from_token(credentials_token, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserRegister, db: Session = Depends(get_db)):
    """Register a new user"""
    existing_user = get_user_by_email(db, user_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = create_user(
        db,
        email=user_data.email,
        password=user_data.password,
        full_name=user_data.full_name,
    )
    return user


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Login and get access token"""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(seconds=settings.jwt_expiration)
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=access_token_expires,
    )

    response = JSONResponse(
        content={"access_token": access_token, "token_type": "bearer"}
    )
    _set_access_token_cookie(response, access_token)
    return response


@router.post("/logout")
async def logout():
    """Clear auth cookie"""
    response = JSONResponse(content={"detail": "Logged out"})
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, path="/")
    return response


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return current_user
