from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import httpx
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.social_post_history import SocialPostHistory
from app.services.news_research_service import (
    build_news_bundle,
    estimate_engagement_score,
    suggested_platforms_for_headline,
)
from app.services.pinterest_service import create_pin, get_valid_pinterest_access_token
from app.services.x_service import create_tweet, get_valid_x_access_token
from app.services.mastodon_service import (
    create_status,
    get_valid_mastodon_access_token,
    upload_media,
)
from app.services.bluesky_service import create_post, get_valid_bluesky_session, upload_blob
from app.services.devto_service import create_article as create_devto_article, get_devto_api_key
from app.services.hashnode_service import get_hashnode_token, publish_article as publish_hashnode_article
from app.services.blogger_service import create_post as create_blogger_post, get_valid_blogger_access_token
from app.services.reddit_service import get_valid_reddit_access_token, submit_link_post
from app.services.http_ssl import httpx_verify
from app.services.social_account_service import get_account_metadata, get_user_social_account
from app.services.social_media_storage import public_image_url, resolve_media_path, save_png_base64
from app.schemas.social_post import (
    SocialPostGenerateRequest,
    SocialPostGenerateResponse,
    NewsResultItem,
    SocialPostHistorySaveRequest,
    SocialPostHistoryItemResponse,
    SocialPostPublishRequest,
    SocialPostPublishResponse,
    SocialPostPublishResultItem,
    SocialPostImageUploadRequest,
    SocialPostImageUploadResponse,
)

router = APIRouter()


@router.post("/generate", response_model=SocialPostGenerateResponse)
async def generate_social_post(
    request: SocialPostGenerateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Generate a social post headline from a query: fetch 7 Google News results,
    then use the LLM to produce a single compelling headline.
    """
    try:
        bundle = await build_news_bundle(
            request.query,
            template_variant=request.template_variant,
        )
        if not bundle:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No news results found for the given query.",
            )
        items = [
            NewsResultItem(
                title=item.get("title", ""),
                link=item.get("link", ""),
                source=item.get("source", ""),
                snippet=item.get("snippet", ""),
                date=item.get("date", ""),
            )
            for item in bundle.news_results
        ]
        headline = bundle.headline
        word_count = len(headline.replace("\n", " ").split())
        char_count = len(headline)
        summary = (bundle.news_results[0].get("snippet") or "")[:100]
        return SocialPostGenerateResponse(
            headline=headline,
            news_results=items,
            word_count=word_count,
            char_count=char_count,
            estimated_engagement_score=estimate_engagement_score(headline),
            suggested_platforms=suggested_platforms_for_headline(headline),
            hashtags=bundle.hashtags,
            summary=summary,
            description=bundle.description,
            story_description=bundle.story_description,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating social post: {str(e)}",
        )


@router.get("/history", response_model=list[SocialPostHistoryItemResponse])
async def get_social_post_history(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get social post history for the current user."""
    try:
        rows = (
            db.query(SocialPostHistory)
            .filter(SocialPostHistory.user_id == current_user.id)
            .order_by(SocialPostHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            SocialPostHistoryItemResponse(
                id=r.id,
                query=r.query,
                headline=r.headline,
                description=getattr(r, "description", None),
                story_description=getattr(r, "story_description", None),
                hashtags=getattr(r, "hashtags", None),
                settings_snapshot=r.settings_snapshot,
                news_results=r.news_results,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching social post history: {str(e)}",
        )


@router.post("/history", response_model=SocialPostHistoryItemResponse)
async def save_social_post_to_history(
    request: SocialPostHistorySaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a generated social post to history (for later preview and download)."""
    try:
        record = SocialPostHistory(
            user_id=current_user.id,
            query=request.query,
            headline=request.headline,
            description=request.description,
            story_description=request.story_description,
            hashtags=request.hashtags,
            settings_snapshot=request.settings_snapshot,
            news_results=request.news_results,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return SocialPostHistoryItemResponse(
            id=record.id,
            query=record.query,
            headline=record.headline,
            description=getattr(record, "description", None),
            story_description=getattr(record, "story_description", None),
            hashtags=getattr(record, "hashtags", None),
            settings_snapshot=record.settings_snapshot,
            news_results=record.news_results,
            created_at=record.created_at.isoformat() if record.created_at else "",
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saving social post to history: {str(e)}",
        )


@router.post("/upload-image", response_model=SocialPostImageUploadResponse)
async def upload_social_post_image(
    request: SocialPostImageUploadRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        filename = save_png_base64(request.image_base64)
        return SocialPostImageUploadResponse(
            filename=filename,
            url=public_image_url(filename),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error uploading image: {str(e)}",
        )


@router.get("/media/{filename}")
async def get_social_post_media(filename: str):
    """Public image endpoint for external platforms (e.g. Pinterest) to fetch uploaded PNGs."""
    try:
        path = resolve_media_path(filename)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    return FileResponse(path, media_type="image/png")


@router.post("/publish", response_model=SocialPostPublishResponse)
async def publish_social_post(
    request: SocialPostPublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not request.platforms:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least one platform")

    image_url = None
    if request.image_base64:
        try:
            filename = save_png_base64(request.image_base64)
            image_url = public_image_url(filename)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    description_parts = [request.description or "", request.story_description or ""]
    if request.hashtags:
        description_parts.append(" ".join(request.hashtags))
    combined_description = "\n\n".join(p for p in description_parts if p.strip()) or None

    results: list[SocialPostPublishResultItem] = []

    for platform in request.platforms:
        if platform == "pinterest":
            results.append(
                await _publish_to_pinterest(
                    db,
                    current_user.id,
                    headline=request.headline,
                    description=combined_description,
                    source_url=request.source_url,
                    image_url=image_url,
                    platform_options=(
                        request.platform_options.pinterest
                        if request.platform_options and request.platform_options.pinterest
                        else {}
                    ),
                )
            )
        elif platform == "x":
            results.append(
                await _publish_to_x(
                    db,
                    current_user.id,
                    headline=request.headline,
                    description=combined_description,
                    source_url=request.source_url,
                )
            )
        elif platform == "mastodon":
            results.append(
                await _publish_to_mastodon(
                    db,
                    current_user.id,
                    headline=request.headline,
                    description=combined_description,
                    source_url=request.source_url,
                    image_url=image_url,
                )
            )
        elif platform == "bluesky":
            results.append(
                await _publish_to_bluesky(
                    db,
                    current_user.id,
                    headline=request.headline,
                    description=combined_description,
                    source_url=request.source_url,
                    image_url=image_url,
                )
            )
        elif platform == "devto":
            results.append(
                await _publish_to_devto(
                    db,
                    current_user.id,
                    headline=request.headline,
                    description=combined_description,
                    source_url=request.source_url,
                )
            )
        elif platform == "hashnode":
            results.append(
                await _publish_to_hashnode(
                    db,
                    current_user.id,
                    headline=request.headline,
                    description=combined_description,
                    source_url=request.source_url,
                )
            )
        elif platform == "blogger":
            results.append(
                await _publish_to_blogger(
                    db,
                    current_user.id,
                    headline=request.headline,
                    description=combined_description,
                    source_url=request.source_url,
                    platform_options=(
                        request.platform_options.blogger
                        if request.platform_options and request.platform_options.blogger
                        else {}
                    ),
                )
            )
        elif platform == "reddit":
            results.append(
                await _publish_to_reddit(
                    db,
                    current_user.id,
                    headline=request.headline,
                    description=combined_description,
                    source_url=request.source_url,
                    image_url=image_url,
                    platform_options=(
                        request.platform_options.reddit
                        if request.platform_options and request.platform_options.reddit
                        else {}
                    ),
                )
            )
        else:
            results.append(
                SocialPostPublishResultItem(
                    platform=platform,
                    success=False,
                    error="Platform publishing is not implemented yet",
                )
            )

    return SocialPostPublishResponse(results=results)


async def _publish_to_pinterest(
    db: Session,
    user_id: int,
    *,
    headline: str,
    description: str | None,
    source_url: str | None,
    image_url: str | None,
    platform_options: dict,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "pinterest")
    if not account:
        return SocialPostPublishResultItem(
            platform="pinterest",
            success=False,
            error="Pinterest account not connected",
        )

    if not image_url:
        return SocialPostPublishResultItem(
            platform="pinterest",
            success=False,
            error="Image is required for Pinterest pins",
        )

    metadata = get_account_metadata(account)
    board_id = platform_options.get("board_id") or metadata.get("default_board_id")
    if not board_id:
        return SocialPostPublishResultItem(
            platform="pinterest",
            success=False,
            error="No Pinterest board selected. Choose a default board in Social accounts.",
        )

    try:
        access_token = await get_valid_pinterest_access_token(db, user_id)
        pin = await create_pin(
            access_token,
            board_id=str(board_id),
            title=headline,
            description=description,
            link=source_url,
            image_url=image_url,
        )
        pin_id = pin.get("id")
        return SocialPostPublishResultItem(
            platform="pinterest",
            success=True,
            external_id=str(pin_id) if pin_id else None,
            external_url=f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform="pinterest",
            success=False,
            error=str(exc),
        )


def _build_post_text(headline: str, description: str | None, source_url: str | None) -> str:
    parts = [headline.strip()]
    if description and description.strip():
        parts.append(description.strip())
    if source_url and source_url.strip():
        parts.append(source_url.strip())
    return "\n\n".join(parts)


async def _download_image_bytes(image_url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.get(image_url)
    response.raise_for_status()
    return response.content


async def _publish_to_x(
    db: Session,
    user_id: int,
    *,
    headline: str,
    description: str | None,
    source_url: str | None,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "x")
    if not account:
        return SocialPostPublishResultItem(
            platform="x",
            success=False,
            error="X account not connected",
        )

    text = _build_post_text(headline, description, source_url)
    try:
        access_token = await get_valid_x_access_token(db, user_id)
        tweet = await create_tweet(access_token, text)
        tweet_id = tweet.get("id")
        metadata = get_account_metadata(account)
        username = metadata.get("username")
        external_url = (
            f"https://x.com/{username}/status/{tweet_id}"
            if username and tweet_id
            else (f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None)
        )
        return SocialPostPublishResultItem(
            platform="x",
            success=True,
            external_id=str(tweet_id) if tweet_id else None,
            external_url=external_url,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform="x",
            success=False,
            error=str(exc),
        )


async def _publish_to_mastodon(
    db: Session,
    user_id: int,
    *,
    headline: str,
    description: str | None,
    source_url: str | None,
    image_url: str | None,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "mastodon")
    if not account:
        return SocialPostPublishResultItem(
            platform="mastodon",
            success=False,
            error="Mastodon account not connected",
        )

    status_text = _build_post_text(headline, description, source_url)
    try:
        access_token, instance_url = await get_valid_mastodon_access_token(db, user_id)
        media_ids: list[str] = []
        if image_url:
            image_bytes = await _download_image_bytes(image_url)
            media_ids.append(
                await upload_media(instance_url, access_token, image_bytes)
            )
        status = await create_status(
            instance_url,
            access_token,
            status=status_text,
            media_ids=media_ids or None,
        )
        status_id = status.get("id")
        return SocialPostPublishResultItem(
            platform="mastodon",
            success=True,
            external_id=str(status_id) if status_id else None,
            external_url=status.get("url"),
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform="mastodon",
            success=False,
            error=str(exc),
        )


async def _publish_to_bluesky(
    db: Session,
    user_id: int,
    *,
    headline: str,
    description: str | None,
    source_url: str | None,
    image_url: str | None,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "bluesky")
    if not account:
        return SocialPostPublishResultItem(
            platform="bluesky",
            success=False,
            error="Bluesky account not connected",
        )

    text = _build_post_text(headline, description, source_url)
    try:
        access_jwt, did = await get_valid_bluesky_session(db, user_id)
        embed = None
        if image_url:
            image_bytes = await _download_image_bytes(image_url)
            blob = await upload_blob(access_jwt, image_bytes)
            if blob:
                embed = {
                    "$type": "app.bsky.embed.images",
                    "images": [
                        {
                            "alt": headline[:1000],
                            "image": blob,
                        }
                    ],
                }
        result = await create_post(
            access_jwt,
            did=did,
            text=text,
            embed=embed,
        )
        uri = result.get("uri")
        handle = get_account_metadata(account).get("handle")
        external_url = None
        if uri and handle:
            rkey = uri.rsplit("/", 1)[-1]
            external_url = f"https://bsky.app/profile/{handle}/post/{rkey}"
        return SocialPostPublishResultItem(
            platform="bluesky",
            success=True,
            external_id=uri,
            external_url=external_url,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform="bluesky",
            success=False,
            error=str(exc),
        )


def _build_article_markdown(
    headline: str,
    description: str | None,
    source_url: str | None,
) -> str:
    parts = [f"# {headline.strip()}"]
    if description and description.strip():
        parts.append(description.strip())
    if source_url and source_url.strip():
        parts.append(f"[Read more]({source_url.strip()})")
    return "\n\n".join(parts)


def _build_article_html(
    headline: str,
    description: str | None,
    source_url: str | None,
) -> str:
    html_parts = [f"<h1>{headline.strip()}</h1>"]
    if description and description.strip():
        for paragraph in description.strip().split("\n\n"):
            html_parts.append(f"<p>{paragraph.strip()}</p>")
    if source_url and source_url.strip():
        html_parts.append(f'<p><a href="{source_url.strip()}">Read more</a></p>')
    return "\n".join(html_parts)


async def _publish_to_devto(
    db: Session,
    user_id: int,
    *,
    headline: str,
    description: str | None,
    source_url: str | None,
) -> SocialPostPublishResultItem:
    try:
        api_key = get_devto_api_key(db, user_id)
        article = await create_devto_article(
            api_key,
            title=headline,
            body_markdown=_build_article_markdown(headline, description, source_url),
        )
        article_id = article.get("id")
        article_url = article.get("url")
        return SocialPostPublishResultItem(
            platform="devto",
            success=True,
            external_id=str(article_id) if article_id else None,
            external_url=article_url,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform="devto",
            success=False,
            error=str(exc),
        )


async def _publish_to_hashnode(
    db: Session,
    user_id: int,
    *,
    headline: str,
    description: str | None,
    source_url: str | None,
) -> SocialPostPublishResultItem:
    try:
        token = get_hashnode_token(db, user_id)
        post = await publish_hashnode_article(
            token,
            title=headline,
            content_markdown=_build_article_markdown(headline, description, source_url),
        )
        return SocialPostPublishResultItem(
            platform="hashnode",
            success=True,
            external_id=str(post.get("id")) if post.get("id") else None,
            external_url=post.get("url"),
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform="hashnode",
            success=False,
            error=str(exc),
        )


async def _publish_to_blogger(
    db: Session,
    user_id: int,
    *,
    headline: str,
    description: str | None,
    source_url: str | None,
    platform_options: dict,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "blogger")
    if not account:
        return SocialPostPublishResultItem(
            platform="blogger",
            success=False,
            error="Blogger account not connected",
        )

    metadata = get_account_metadata(account)
    blog_id = platform_options.get("blog_id") or metadata.get("default_blog_id")
    if not blog_id:
        return SocialPostPublishResultItem(
            platform="blogger",
            success=False,
            error="No Blogger blog selected. Choose a default blog in Social accounts.",
        )

    try:
        access_token = await get_valid_blogger_access_token(db, user_id)
        post = await create_blogger_post(
            access_token,
            blog_id=str(blog_id),
            title=headline,
            content=_build_article_html(headline, description, source_url),
        )
        post_id = post.get("id")
        return SocialPostPublishResultItem(
            platform="blogger",
            success=True,
            external_id=str(post_id) if post_id else None,
            external_url=post.get("url"),
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform="blogger",
            success=False,
            error=str(exc),
        )


async def _publish_to_reddit(
    db: Session,
    user_id: int,
    *,
    headline: str,
    description: str | None,
    source_url: str | None,
    image_url: str | None,
    platform_options: dict,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "reddit")
    if not account:
        return SocialPostPublishResultItem(
            platform="reddit",
            success=False,
            error="Reddit account not connected",
        )

    metadata = get_account_metadata(account)
    subreddit = platform_options.get("subreddit") or metadata.get("default_subreddit")
    if not subreddit:
        return SocialPostPublishResultItem(
            platform="reddit",
            success=False,
            error="No subreddit selected. Choose a default subreddit in Social accounts.",
        )

    post_url = source_url or image_url
    if not post_url:
        return SocialPostPublishResultItem(
            platform="reddit",
            success=False,
            error="A source link or image URL is required for Reddit link posts",
        )

    try:
        access_token, credentials, username = await get_valid_reddit_access_token(db, user_id)
        result = await submit_link_post(
            access_token,
            credentials,
            subreddit=str(subreddit),
            title=headline,
            url=post_url,
            username=username or None,
        )
        json_data = result.get("json", {}).get("data", {})
        post_id = json_data.get("id") or json_data.get("name")
        post_url_result = json_data.get("url")
        if not post_url_result and post_id:
            post_url_result = f"https://www.reddit.com/comments/{post_id.removeprefix('t3_')}/"
        return SocialPostPublishResultItem(
            platform="reddit",
            success=True,
            external_id=str(post_id) if post_id else None,
            external_url=post_url_result,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(
            platform="reddit",
            success=False,
            error=str(exc),
        )
