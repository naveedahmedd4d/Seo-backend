from typing import List
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.social_account import (
    ApiKeyConnectRequest,
    ApiKeyConnectResponse,
    BloggerBlogItem,
    BloggerBlogUpdateRequest,
    BloggerBlogsResponse,
    BlueskyConnectRequest,
    BlueskyConnectResponse,
    MastodonConnectRequest,
    MastodonSetupResponse,
    OAuthConnectRequest,
    OAuthSetupResponse,
    PinterestAppCredentialsRequest,
    PinterestBoardItem,
    PinterestBoardsResponse,
    PinterestBoardUpdateRequest,
    PinterestConnectRequest,
    PinterestSetupResponse,
    RedditSubredditItem,
    RedditSubredditsResponse,
    RedditSubredditUpdateRequest,
    SocialAccountConnectResponse,
    SocialAccountResponse,
)
from app.services.auth_service import (
    create_oauth_state_token,
    decode_oauth_state,
    get_user_from_oauth_state,
)
from app.services.blogger_service import (
    build_authorization_url as build_blogger_authorization_url,
    fetch_blogs,
    get_valid_blogger_access_token,
    save_blogger_account_from_code,
    set_default_blog,
    start_blogger_oauth_pkce,
)
from app.services.bluesky_service import save_bluesky_account_from_credentials
from app.services.devto_service import save_devto_account_from_api_key
from app.services.hashnode_service import save_hashnode_account_from_token
from app.services.mastodon_service import (
    build_authorization_url as build_mastodon_authorization_url,
    save_mastodon_account_from_code,
)
from app.services.reddit_service import (
    build_authorization_url as build_reddit_authorization_url,
    fetch_subscribed_subreddits,
    get_valid_reddit_access_token,
    save_reddit_account_from_code,
    set_default_subreddit,
)
from app.services.pinterest_service import (
    build_authorization_url as build_pinterest_authorization_url,
    fetch_boards,
    get_valid_pinterest_access_token,
    save_pinterest_account_from_code,
    set_default_board,
)
from app.services.social_account_service import (
    delete_social_account,
    get_account_metadata,
    get_social_account_by_id,
    get_user_social_account,
    get_user_social_accounts,
)
from app.services.social_platform_app_service import (
    CREDENTIAL_ONLY_PLATFORMS,
    delete_user_platform_app,
    get_blogger_app_credentials,
    get_blogger_redirect_uri,
    get_mastodon_app_credentials,
    get_mastodon_redirect_uri,
    get_reddit_app_credentials,
    get_reddit_redirect_uri,
    get_pinterest_app_credentials,
    get_pinterest_redirect_uri,
    get_user_platform_app,
    get_x_app_credentials,
    get_x_redirect_uri,
    upsert_user_platform_app,
    user_has_blogger_app,
    user_has_mastodon_app,
    user_has_pinterest_app,
    user_has_reddit_app,
    user_has_x_app,
)
from app.services.x_service import (
    build_authorization_url as build_x_authorization_url,
    save_x_account_from_code,
    start_x_oauth_pkce,
)

router = APIRouter()


def _account_to_response(account) -> SocialAccountResponse:
    return SocialAccountResponse(
        id=account.id,
        platform=account.platform,
        account_label=account.account_label,
        connected=True,
        metadata=get_account_metadata(account),
        created_at=account.created_at.isoformat() if account.created_at else "",
        updated_at=account.updated_at.isoformat() if account.updated_at else None,
    )


def _oauth_callback_redirect(platform: str, *, error: str | None = None) -> RedirectResponse:
    frontend = settings.frontend_url.rstrip("/")
    if error:
        params = urlencode({"social_error": error, "platform": platform})
    else:
        params = urlencode({"connected": platform})
    return RedirectResponse(f"{frontend}/dashboard/social-post?{params}")


@router.get("", response_model=List[SocialAccountResponse])
async def list_social_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accounts = get_user_social_accounts(db, current_user.id)
    return [_account_to_response(account) for account in accounts]


# --- Pinterest ---


@router.get("/pinterest/setup", response_model=PinterestSetupResponse)
async def pinterest_setup(
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app_row = get_user_platform_app(db, current_user.id, "pinterest")
    return PinterestSetupResponse(
        redirect_uri=get_pinterest_redirect_uri(http_request),
        has_app_credentials=user_has_pinterest_app(db, current_user.id),
        client_id=app_row.client_id if app_row else None,
    )


@router.put("/pinterest/app", response_model=PinterestSetupResponse)
async def save_pinterest_app_credentials(
    request: PinterestAppCredentialsRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        upsert_user_platform_app(
            db,
            current_user.id,
            "pinterest",
            request.client_id,
            request.client_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return PinterestSetupResponse(
        redirect_uri=get_pinterest_redirect_uri(http_request),
        has_app_credentials=True,
        client_id=request.client_id.strip(),
    )


@router.post("/pinterest/connect", response_model=SocialAccountConnectResponse)
async def pinterest_connect(
    request: PinterestConnectRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        upsert_user_platform_app(
            db,
            current_user.id,
            "pinterest",
            request.client_id,
            request.client_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    redirect_uri = get_pinterest_redirect_uri(http_request)
    credentials = get_pinterest_app_credentials(
        db, current_user.id, redirect_uri=redirect_uri
    )
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save Pinterest app credentials",
        )

    state = create_oauth_state_token(current_user, "pinterest")
    return SocialAccountConnectResponse(
        authorization_url=build_pinterest_authorization_url(credentials, state)
    )


@router.get("/pinterest/callback")
async def pinterest_callback(
    http_request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_callback_redirect("pinterest", error=error)
    if not code or not state:
        return _oauth_callback_redirect("pinterest", error="missing_code")

    user = get_user_from_oauth_state(state, db, "pinterest")
    if not user:
        return _oauth_callback_redirect("pinterest", error="invalid_state")

    redirect_uri = get_pinterest_redirect_uri(http_request)
    try:
        await save_pinterest_account_from_code(
            db, user.id, code, redirect_uri=redirect_uri
        )
    except Exception as exc:
        return _oauth_callback_redirect("pinterest", error=str(exc))

    return _oauth_callback_redirect("pinterest")


@router.get("/pinterest/boards", response_model=PinterestBoardsResponse)
async def pinterest_boards(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_user_social_account(db, current_user.id, "pinterest")
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pinterest not connected")

    access_token = await get_valid_pinterest_access_token(db, current_user.id)
    boards_raw = await fetch_boards(access_token)
    metadata = get_account_metadata(account)
    boards = [
        PinterestBoardItem(id=str(b["id"]), name=str(b.get("name") or "Board"))
        for b in boards_raw
        if b.get("id")
    ]
    return PinterestBoardsResponse(
        boards=boards,
        default_board_id=metadata.get("default_board_id"),
    )


@router.put("/pinterest/board", response_model=SocialAccountResponse)
async def pinterest_set_default_board(
    request: PinterestBoardUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_user_social_account(db, current_user.id, "pinterest")
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pinterest not connected")

    access_token = await get_valid_pinterest_access_token(db, current_user.id)
    boards_raw = await fetch_boards(access_token)
    match = next((b for b in boards_raw if str(b.get("id")) == request.board_id), None)
    if not match:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Board not found")

    updated = set_default_board(
        db,
        account,
        request.board_id,
        str(match.get("name") or "Board"),
    )
    return _account_to_response(updated)


# --- X (Twitter) ---


@router.get("/x/setup", response_model=OAuthSetupResponse)
async def x_setup(
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app_row = get_user_platform_app(db, current_user.id, "x")
    return OAuthSetupResponse(
        redirect_uri=get_x_redirect_uri(http_request),
        has_app_credentials=user_has_x_app(db, current_user.id),
        client_id=app_row.client_id if app_row else None,
        developer_portal_url="https://developer.x.com/en/portal/projects-and-apps",
    )


@router.post("/x/connect", response_model=SocialAccountConnectResponse)
async def x_connect(
    request: OAuthConnectRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        upsert_user_platform_app(
            db,
            current_user.id,
            "x",
            request.client_id,
            request.client_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    redirect_uri = get_x_redirect_uri(http_request)
    credentials = get_x_app_credentials(db, current_user.id, redirect_uri=redirect_uri)
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save X app credentials",
        )

    code_verifier, code_challenge = start_x_oauth_pkce()
    state = create_oauth_state_token(
        current_user,
        "x",
        extra={"code_verifier": code_verifier},
    )
    return SocialAccountConnectResponse(
        authorization_url=build_x_authorization_url(
            credentials,
            state,
            code_challenge=code_challenge,
        )
    )


@router.get("/x/callback")
async def x_callback(
    http_request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_callback_redirect("x", error=error)
    if not code or not state:
        return _oauth_callback_redirect("x", error="missing_code")

    user_payload, extra = decode_oauth_state(state, "x")
    if not user_payload:
        return _oauth_callback_redirect("x", error="invalid_state")

    code_verifier = (extra or {}).get("code_verifier")
    if not code_verifier:
        return _oauth_callback_redirect("x", error="missing_pkce_verifier")

    user = get_user_from_oauth_state(state, db, "x")
    if not user:
        return _oauth_callback_redirect("x", error="invalid_state")

    redirect_uri = get_x_redirect_uri(http_request)
    try:
        await save_x_account_from_code(
            db, user.id, code, str(code_verifier), redirect_uri=redirect_uri
        )
    except Exception as exc:
        return _oauth_callback_redirect("x", error=str(exc))

    return _oauth_callback_redirect("x")


# --- Mastodon ---


@router.get("/mastodon/setup", response_model=MastodonSetupResponse)
async def mastodon_setup(
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    creds = get_mastodon_app_credentials(db, current_user.id)
    app_row = get_user_platform_app(db, current_user.id, "mastodon")
    return MastodonSetupResponse(
        redirect_uri=get_mastodon_redirect_uri(http_request),
        has_app_credentials=user_has_mastodon_app(db, current_user.id),
        client_id=app_row.client_id if app_row else None,
        instance_url=creds.instance_url if creds else None,
    )


@router.post("/mastodon/connect", response_model=SocialAccountConnectResponse)
async def mastodon_connect(
    request: MastodonConnectRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        upsert_user_platform_app(
            db,
            current_user.id,
            "mastodon",
            request.client_id,
            request.client_secret,
            instance_url=request.instance_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    redirect_uri = get_mastodon_redirect_uri(http_request)
    credentials = get_mastodon_app_credentials(
        db, current_user.id, redirect_uri=redirect_uri
    )
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save Mastodon app credentials",
        )

    state = create_oauth_state_token(current_user, "mastodon")
    return SocialAccountConnectResponse(
        authorization_url=build_mastodon_authorization_url(credentials, state)
    )


@router.get("/mastodon/callback")
async def mastodon_callback(
    http_request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_callback_redirect("mastodon", error=error)
    if not code or not state:
        return _oauth_callback_redirect("mastodon", error="missing_code")

    user = get_user_from_oauth_state(state, db, "mastodon")
    if not user:
        return _oauth_callback_redirect("mastodon", error="invalid_state")

    redirect_uri = get_mastodon_redirect_uri(http_request)
    try:
        await save_mastodon_account_from_code(
            db, user.id, code, redirect_uri=redirect_uri
        )
    except Exception as exc:
        return _oauth_callback_redirect("mastodon", error=str(exc))

    return _oauth_callback_redirect("mastodon")


# --- Bluesky ---


@router.post("/bluesky/connect", response_model=BlueskyConnectResponse)
async def bluesky_connect(
    request: BlueskyConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not request.identifier.strip() or not request.app_password.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bluesky handle and app password are required",
        )
    try:
        account = await save_bluesky_account_from_credentials(
            db,
            current_user.id,
            request.identifier,
            request.app_password,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return BlueskyConnectResponse(
        connected=True,
        account_label=account.account_label,
    )


# --- Dev.to ---


@router.post("/devto/connect", response_model=ApiKeyConnectResponse)
async def devto_connect(
    request: ApiKeyConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not request.api_key.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dev.to API key is required")
    try:
        account = await save_devto_account_from_api_key(db, current_user.id, request.api_key)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ApiKeyConnectResponse(connected=True, account_label=account.account_label)


# --- Hashnode ---


@router.post("/hashnode/connect", response_model=ApiKeyConnectResponse)
async def hashnode_connect(
    request: ApiKeyConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not request.api_key.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hashnode API token is required")
    try:
        account = await save_hashnode_account_from_token(db, current_user.id, request.api_key)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ApiKeyConnectResponse(connected=True, account_label=account.account_label)


# --- Blogger ---


@router.get("/blogger/setup", response_model=OAuthSetupResponse)
async def blogger_setup(
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app_row = get_user_platform_app(db, current_user.id, "blogger")
    return OAuthSetupResponse(
        redirect_uri=get_blogger_redirect_uri(http_request),
        has_app_credentials=user_has_blogger_app(db, current_user.id),
        client_id=app_row.client_id if app_row else None,
        developer_portal_url="https://console.cloud.google.com/apis/credentials",
    )


@router.post("/blogger/connect", response_model=SocialAccountConnectResponse)
async def blogger_connect(
    request: OAuthConnectRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        upsert_user_platform_app(
            db,
            current_user.id,
            "blogger",
            request.client_id,
            request.client_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    redirect_uri = get_blogger_redirect_uri(http_request)
    credentials = get_blogger_app_credentials(
        db, current_user.id, redirect_uri=redirect_uri
    )
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save Blogger app credentials",
        )

    code_verifier, code_challenge = start_blogger_oauth_pkce()
    state = create_oauth_state_token(
        current_user,
        "blogger",
        extra={"code_verifier": code_verifier},
    )
    return SocialAccountConnectResponse(
        authorization_url=build_blogger_authorization_url(
            credentials,
            state,
            code_challenge=code_challenge,
        )
    )


@router.get("/blogger/callback")
async def blogger_callback(
    http_request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_callback_redirect("blogger", error=error)
    if not code or not state:
        return _oauth_callback_redirect("blogger", error="missing_code")

    user_payload, extra = decode_oauth_state(state, "blogger")
    if not user_payload:
        return _oauth_callback_redirect("blogger", error="invalid_state")

    code_verifier = (extra or {}).get("code_verifier")
    if not code_verifier:
        return _oauth_callback_redirect("blogger", error="missing_pkce_verifier")

    user = get_user_from_oauth_state(state, db, "blogger")
    if not user:
        return _oauth_callback_redirect("blogger", error="invalid_state")

    redirect_uri = get_blogger_redirect_uri(http_request)
    try:
        await save_blogger_account_from_code(
            db, user.id, code, str(code_verifier), redirect_uri=redirect_uri
        )
    except Exception as exc:
        return _oauth_callback_redirect("blogger", error=str(exc))

    return _oauth_callback_redirect("blogger")


@router.get("/blogger/blogs", response_model=BloggerBlogsResponse)
async def blogger_blogs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_user_social_account(db, current_user.id, "blogger")
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blogger not connected")

    access_token = await get_valid_blogger_access_token(db, current_user.id)
    blogs_raw = await fetch_blogs(access_token)
    metadata = get_account_metadata(account)
    blogs = [
        BloggerBlogItem(id=str(b["id"]), name=str(b.get("name") or "Blog"))
        for b in blogs_raw
        if b.get("id")
    ]
    return BloggerBlogsResponse(
        blogs=blogs,
        default_blog_id=metadata.get("default_blog_id"),
    )


@router.put("/blogger/blog", response_model=SocialAccountResponse)
async def blogger_set_default_blog(
    request: BloggerBlogUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_user_social_account(db, current_user.id, "blogger")
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blogger not connected")

    access_token = await get_valid_blogger_access_token(db, current_user.id)
    blogs_raw = await fetch_blogs(access_token)
    match = next((b for b in blogs_raw if str(b.get("id")) == request.blog_id), None)
    if not match:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Blog not found")

    updated = set_default_blog(
        db,
        account,
        request.blog_id,
        str(match.get("name") or "Blog"),
    )
    return _account_to_response(updated)


# --- Reddit ---


@router.get("/reddit/setup", response_model=OAuthSetupResponse)
async def reddit_setup(
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app_row = get_user_platform_app(db, current_user.id, "reddit")
    return OAuthSetupResponse(
        redirect_uri=get_reddit_redirect_uri(http_request),
        has_app_credentials=user_has_reddit_app(db, current_user.id),
        client_id=app_row.client_id if app_row else None,
        developer_portal_url="https://www.reddit.com/prefs/apps",
    )


@router.post("/reddit/connect", response_model=SocialAccountConnectResponse)
async def reddit_connect(
    request: OAuthConnectRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        upsert_user_platform_app(
            db,
            current_user.id,
            "reddit",
            request.client_id,
            request.client_secret,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    redirect_uri = get_reddit_redirect_uri(http_request)
    credentials = get_reddit_app_credentials(
        db, current_user.id, redirect_uri=redirect_uri
    )
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save Reddit app credentials",
        )

    state = create_oauth_state_token(current_user, "reddit")
    return SocialAccountConnectResponse(
        authorization_url=build_reddit_authorization_url(credentials, state)
    )


@router.get("/reddit/callback")
async def reddit_callback(
    http_request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if error:
        return _oauth_callback_redirect("reddit", error=error)
    if not code or not state:
        return _oauth_callback_redirect("reddit", error="missing_code")

    user = get_user_from_oauth_state(state, db, "reddit")
    if not user:
        return _oauth_callback_redirect("reddit", error="invalid_state")

    redirect_uri = get_reddit_redirect_uri(http_request)
    try:
        await save_reddit_account_from_code(
            db, user.id, code, redirect_uri=redirect_uri
        )
    except Exception as exc:
        return _oauth_callback_redirect("reddit", error=str(exc))

    return _oauth_callback_redirect("reddit")


@router.get("/reddit/subreddits", response_model=RedditSubredditsResponse)
async def reddit_subreddits(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_user_social_account(db, current_user.id, "reddit")
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reddit not connected")

    access_token, credentials, username = await get_valid_reddit_access_token(db, current_user.id)
    subs_raw = await fetch_subscribed_subreddits(
        access_token,
        credentials,
        username=username or None,
    )
    metadata = get_account_metadata(account)
    subreddits = [
        RedditSubredditItem(name=str(s["name"]), title=str(s.get("title") or s["name"]))
        for s in subs_raw
        if s.get("name")
    ]
    return RedditSubredditsResponse(
        subreddits=subreddits,
        default_subreddit=metadata.get("default_subreddit"),
    )


@router.put("/reddit/subreddit", response_model=SocialAccountResponse)
async def reddit_set_default_subreddit(
    request: RedditSubredditUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_user_social_account(db, current_user.id, "reddit")
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reddit not connected")

    subreddit = request.subreddit.strip().removeprefix("r/").removeprefix("/")
    if not subreddit:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Subreddit is required")

    access_token, credentials, username = await get_valid_reddit_access_token(db, current_user.id)
    subs_raw = await fetch_subscribed_subreddits(
        access_token,
        credentials,
        username=username or None,
    )
    match = next((s for s in subs_raw if str(s.get("name", "")).lower() == subreddit.lower()), None)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subreddit not found in your subscriptions",
        )

    updated = set_default_subreddit(db, account, str(match["name"]))
    return _account_to_response(updated)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_social_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = get_social_account_by_id(db, account_id, current_user.id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    platform = account.platform
    delete_social_account(db, account)
    if platform not in CREDENTIAL_ONLY_PLATFORMS:
        delete_user_platform_app(db, current_user.id, platform)
    return None
