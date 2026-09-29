"""Platform-specific publish helpers for social posts."""

from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.schemas.social_post import SocialPostPublishResultItem
from app.services.blogger_service import create_post as create_blogger_post, get_valid_blogger_access_token
from app.services.bluesky_service import create_post, get_valid_bluesky_session, upload_blob
from app.services.devto_service import create_article as create_devto_article, get_devto_api_key
from app.services.facebook_service import get_valid_facebook_page_context, publish_page_photo
from app.services.hashnode_service import get_hashnode_token, publish_article as publish_hashnode_article
from app.services.http_ssl import httpx_verify
from app.services.instagram_service import get_valid_instagram_context, publish_instagram_image_post
from app.services.linkedin_service import create_text_share, get_valid_linkedin_access_token
from app.services.mastodon_service import create_status, get_valid_mastodon_access_token, upload_media
from app.services.pinterest_service import (
    create_pin,
    get_valid_pinterest_access_token,
    resolve_pinterest_board_id,
)
from app.services.reddit_service import get_valid_reddit_access_token, submit_link_post
from app.services.social_account_service import get_account_metadata, get_user_social_account
from app.services.social_post_copy_service import PublishCopyContext, build_platform_copy
from app.services.x_service import create_tweet, get_valid_x_access_token, upload_media as upload_x_media


async def download_image_bytes(image_url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        response = await client.get(image_url)
    response.raise_for_status()
    return response.content


async def publish_to_platform(
    db: Session,
    user_id: int,
    platform: str,
    *,
    ctx: PublishCopyContext,
    image_url: str | None,
    image_bytes: bytes | None = None,
    platform_options: dict | None = None,
) -> SocialPostPublishResultItem:
    platform_options = platform_options or {}
    copy = build_platform_copy(platform, ctx)

    if platform == "pinterest":
        return await _publish_to_pinterest(
            db,
            user_id,
            title=str(copy.get("title") or ctx.headline),
            description=copy.get("description"),
            source_url=None,
            image_url=image_url,
            platform_options=platform_options,
        )
    if platform == "x":
        return await _publish_to_x(
            db,
            user_id,
            text=str(copy.get("text") or ""),
            image_bytes=image_bytes,
        )
    if platform == "mastodon":
        return await _publish_to_mastodon(
            db,
            user_id,
            text=str(copy.get("text") or ""),
            image_url=image_url,
        )
    if platform == "bluesky":
        return await _publish_to_bluesky(
            db,
            user_id,
            text=str(copy.get("text") or ""),
            headline=ctx.headline,
            image_url=image_url,
        )
    if platform == "devto":
        return await _publish_to_devto(
            db,
            user_id,
            title=str(copy.get("title") or ctx.headline),
            body_markdown=str(copy.get("body_markdown") or ""),
        )
    if platform == "hashnode":
        return await _publish_to_hashnode(
            db,
            user_id,
            title=str(copy.get("title") or ctx.headline),
            content_markdown=str(copy.get("content_markdown") or ""),
        )
    if platform == "blogger":
        return await _publish_to_blogger(
            db,
            user_id,
            title=str(copy.get("title") or ctx.headline),
            content_html=str(copy.get("content_html") or ""),
            platform_options=platform_options,
        )
    if platform == "reddit":
        return await _publish_to_reddit(
            db,
            user_id,
            title=str(copy.get("title") or ctx.headline),
            source_url=ctx.source_url,
            image_url=image_url,
            platform_options=platform_options,
        )
    if platform == "linkedin":
        return await _publish_to_linkedin(
            db,
            user_id,
            text=str(copy.get("text") or ""),
            source_url=ctx.source_url,
        )
    if platform == "facebook":
        return await _publish_to_facebook(
            db,
            user_id,
            message=str(copy.get("message") or ""),
            source_url=ctx.source_url,
            image_url=image_url,
            image_bytes=image_bytes,
            platform_options=platform_options,
        )
    if platform == "instagram":
        return await _publish_to_instagram(
            db,
            user_id,
            caption=str(copy.get("caption") or ""),
            image_url=image_url,
            username_hint=None,
            platform_options=platform_options,
        )

    return SocialPostPublishResultItem(
        platform=platform,
        success=False,
        error="Platform publishing is not implemented yet",
    )


async def _publish_to_pinterest(
    db: Session,
    user_id: int,
    *,
    title: str,
    description: str | None,
    source_url: str | None,
    image_url: str | None,
    platform_options: dict,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "pinterest")
    if not account:
        return SocialPostPublishResultItem(platform="pinterest", success=False, error="Pinterest account not connected")
    if not image_url:
        return SocialPostPublishResultItem(platform="pinterest", success=False, error="Image is required for Pinterest pins")

    try:
        access_token = await get_valid_pinterest_access_token(db, user_id)
        board_id = await resolve_pinterest_board_id(
            db, user_id, account, platform_options
        )
    except Exception as exc:
        return SocialPostPublishResultItem(platform="pinterest", success=False, error=str(exc))

    if not board_id:
        return SocialPostPublishResultItem(
            platform="pinterest",
            success=False,
            error="No Pinterest boards found on your account. Create a board on Pinterest first.",
        )

    try:
        pin = await create_pin(
            access_token,
            board_id=str(board_id),
            title=title,
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
        return SocialPostPublishResultItem(platform="pinterest", success=False, error=str(exc))


async def _publish_to_x(
    db: Session,
    user_id: int,
    *,
    text: str,
    image_bytes: bytes | None = None,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "x")
    if not account:
        return SocialPostPublishResultItem(platform="x", success=False, error="X account not connected")

    try:
        access_token = await get_valid_x_access_token(db, user_id)
        media_ids: list[str] | None = None
        if image_bytes:
            media_id = await upload_x_media(access_token, image_bytes)
            media_ids = [media_id]
        tweet = await create_tweet(access_token, text, media_ids=media_ids)
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
        return SocialPostPublishResultItem(platform="x", success=False, error=str(exc))


async def _publish_to_mastodon(
    db: Session,
    user_id: int,
    *,
    text: str,
    image_url: str | None,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "mastodon")
    if not account:
        return SocialPostPublishResultItem(platform="mastodon", success=False, error="Mastodon account not connected")

    try:
        access_token, instance_url = await get_valid_mastodon_access_token(db, user_id)
        media_ids: list[str] = []
        if image_url:
            image_bytes = await download_image_bytes(image_url)
            media_ids.append(await upload_media(instance_url, access_token, image_bytes))
        status = await create_status(instance_url, access_token, status=text, media_ids=media_ids or None)
        status_id = status.get("id")
        return SocialPostPublishResultItem(
            platform="mastodon",
            success=True,
            external_id=str(status_id) if status_id else None,
            external_url=status.get("url"),
        )
    except Exception as exc:
        return SocialPostPublishResultItem(platform="mastodon", success=False, error=str(exc))


async def _publish_to_bluesky(
    db: Session,
    user_id: int,
    *,
    text: str,
    headline: str,
    image_url: str | None,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "bluesky")
    if not account:
        return SocialPostPublishResultItem(platform="bluesky", success=False, error="Bluesky account not connected")

    try:
        access_jwt, did = await get_valid_bluesky_session(db, user_id)
        embed = None
        if image_url:
            image_bytes = await download_image_bytes(image_url)
            blob = await upload_blob(access_jwt, image_bytes)
            if blob:
                embed = {
                    "$type": "app.bsky.embed.images",
                    "images": [{"alt": headline[:1000], "image": blob}],
                }
        result = await create_post(access_jwt, did=did, text=text, embed=embed)
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
        return SocialPostPublishResultItem(platform="bluesky", success=False, error=str(exc))


async def _publish_to_devto(
    db: Session,
    user_id: int,
    *,
    title: str,
    body_markdown: str,
) -> SocialPostPublishResultItem:
    try:
        api_key = get_devto_api_key(db, user_id)
        article = await create_devto_article(api_key, title=title, body_markdown=body_markdown)
        article_id = article.get("id")
        return SocialPostPublishResultItem(
            platform="devto",
            success=True,
            external_id=str(article_id) if article_id else None,
            external_url=article.get("url"),
        )
    except Exception as exc:
        return SocialPostPublishResultItem(platform="devto", success=False, error=str(exc))


async def _publish_to_hashnode(
    db: Session,
    user_id: int,
    *,
    title: str,
    content_markdown: str,
) -> SocialPostPublishResultItem:
    try:
        token = get_hashnode_token(db, user_id)
        post = await publish_hashnode_article(token, title=title, content_markdown=content_markdown)
        return SocialPostPublishResultItem(
            platform="hashnode",
            success=True,
            external_id=str(post.get("id")) if post.get("id") else None,
            external_url=post.get("url"),
        )
    except Exception as exc:
        return SocialPostPublishResultItem(platform="hashnode", success=False, error=str(exc))


async def _publish_to_blogger(
    db: Session,
    user_id: int,
    *,
    title: str,
    content_html: str,
    platform_options: dict,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "blogger")
    if not account:
        return SocialPostPublishResultItem(platform="blogger", success=False, error="Blogger account not connected")

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
            title=title,
            content=content_html,
        )
        post_id = post.get("id")
        return SocialPostPublishResultItem(
            platform="blogger",
            success=True,
            external_id=str(post_id) if post_id else None,
            external_url=post.get("url"),
        )
    except Exception as exc:
        return SocialPostPublishResultItem(platform="blogger", success=False, error=str(exc))


async def _publish_to_reddit(
    db: Session,
    user_id: int,
    *,
    title: str,
    source_url: str | None,
    image_url: str | None,
    platform_options: dict,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "reddit")
    if not account:
        return SocialPostPublishResultItem(platform="reddit", success=False, error="Reddit account not connected")

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
            title=title,
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
        return SocialPostPublishResultItem(platform="reddit", success=False, error=str(exc))


async def _publish_to_linkedin(
    db: Session,
    user_id: int,
    *,
    text: str,
    source_url: str | None,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "linkedin")
    if not account:
        return SocialPostPublishResultItem(platform="linkedin", success=False, error="LinkedIn account not connected")

    try:
        access_token, person_urn = await get_valid_linkedin_access_token(db, user_id)
        result = await create_text_share(
            access_token,
            person_urn=person_urn,
            text=text,
            article_url=source_url,
        )
        post_id = result.get("id")
        external_url = f"https://www.linkedin.com/feed/update/{post_id}/" if post_id else None
        return SocialPostPublishResultItem(
            platform="linkedin",
            success=True,
            external_id=str(post_id) if post_id else None,
            external_url=external_url,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(platform="linkedin", success=False, error=str(exc))


async def _publish_to_facebook(
    db: Session,
    user_id: int,
    *,
    message: str,
    source_url: str | None,
    image_url: str | None,
    image_bytes: bytes | None,
    platform_options: dict,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "facebook")
    if not account:
        return SocialPostPublishResultItem(platform="facebook", success=False, error="Facebook account not connected")
    try:
        # History publishing supplies bytes from the PNG saved in local storage.
        # The URL fallback keeps automation flows working when their image comes
        # from an externally hosted article rather than a saved social card.
        resolved_image_bytes = image_bytes
        if resolved_image_bytes is None and image_url:
            resolved_image_bytes = await download_image_bytes(image_url)
        if not resolved_image_bytes:
            return SocialPostPublishResultItem(
                platform="facebook",
                success=False,
                error="Image is required for Facebook Page posts",
            )

        page_id, _page_name, page_token = await get_valid_facebook_page_context(
            db,
            user_id,
            page_id=platform_options.get("page_id"),
        )
        caption = message.strip()
        if source_url:
            caption = f"{caption}\n\n{source_url}" if caption else source_url
        result = await publish_page_photo(
            page_token,
            page_id=page_id,
            caption=caption,
            image_bytes=resolved_image_bytes,
        )
        post_id = result.get("post_id") or result.get("id")
        return SocialPostPublishResultItem(
            platform="facebook",
            success=True,
            external_id=str(post_id) if post_id else None,
            external_url=f"https://www.facebook.com/{post_id}" if post_id else None,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(platform="facebook", success=False, error=str(exc))


async def _publish_to_instagram(
    db: Session,
    user_id: int,
    *,
    caption: str,
    image_url: str | None,
    username_hint: str | None,
    platform_options: dict,
) -> SocialPostPublishResultItem:
    account = get_user_social_account(db, user_id, "instagram")
    if not account:
        return SocialPostPublishResultItem(platform="instagram", success=False, error="Instagram account not connected")
    if not image_url:
        return SocialPostPublishResultItem(
            platform="instagram",
            success=False,
            error="Image is required for Instagram posts",
        )

    try:
        ig_id, username, page_token = await get_valid_instagram_context(
            db,
            user_id,
            instagram_id=platform_options.get("instagram_id"),
        )
        result = await publish_instagram_image_post(
            ig_id,
            page_token,
            image_url=image_url,
            caption=caption,
        )
        media_id = result.get("id")
        display_username = username or username_hint
        external_url = f"https://www.instagram.com/{display_username}/" if display_username else None
        return SocialPostPublishResultItem(
            platform="instagram",
            success=True,
            external_id=str(media_id) if media_id else None,
            external_url=external_url,
        )
    except Exception as exc:
        return SocialPostPublishResultItem(platform="instagram", success=False, error=str(exc))
