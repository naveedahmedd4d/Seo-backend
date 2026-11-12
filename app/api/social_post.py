from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import base64
import httpx
import ipaddress
from urllib.parse import urlparse
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.social_post_history import SocialPostHistory
from app.models.social_post_publication import SocialPostPublication
from app.models.published_post import PublishedPost
from app.models.wordpress_site import WordPressSite
from app.models.social_post_template_prefs import SocialPostTemplatePrefs
import logging

from app.services import article_social_service
from app.services.wordpress_service import get_wp_media, get_wp_post
from app.services.custom_image_search_service import search_social_images
from app.services.news_research_service import (
    build_bundle_from_items,
    build_news_bundle,
    estimate_engagement_score,
    suggested_platforms_for_headline,
)
from app.services.llm_settings_service import (
    resolve_api_key,
    resolve_api_key_optional,
    resolve_llm_credentials,
)
from app.services.openrouter_service import set_llm_context, reset_llm_context
from app.services.http_ssl import httpx_verify
from app.services.social_media_storage import public_image_url, resolve_media_path, save_png_base64
from app.services.social_post_publish_service import (
    HistoryNotFoundError,
    MediaRequiredError,
    ensure_media_saved,
    load_history_post,
    publish_history_post,
)
from app.schemas.social_post import (
    SocialPostGenerateRequest,
    SocialPostGenerateResponse,
    NewsResultItem,
    SocialPostHistorySaveRequest,
    SocialPostHistoryItemResponse,
    SocialPostHistoryDetailResponse,
    SocialPostHistoryPublishRequest,
    SocialPostHistoryMediaRequest,
    SocialPostPublicationResponse,
    SocialPostPublishRequest,
    SocialPostPublishResponse,
    SocialPostImageUploadRequest,
    SocialPostImageUploadResponse,
    SocialPostFetchImageRequest,
    SocialPostFetchImageResponse,
    SocialPostTemplatePresetsResponse,
    SocialPostTemplatePresetsSaveRequest,
    SocialPostTemplatePresetsStore,
)

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FETCH_IMAGE_BYTES = 15 * 1024 * 1024
_BLOCKED_IMAGE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _history_item_response(
    record: SocialPostHistory,
    publications: list[SocialPostPublication] | None = None,
    *,
    http_request: Request | None = None,
) -> SocialPostHistoryItemResponse:
    pubs = publications if publications is not None else list(getattr(record, "publications", []) or [])
    platforms_published = sorted({p.platform for p in pubs if p.status == "success"})
    media_filename = getattr(record, "media_filename", None)
    return SocialPostHistoryItemResponse(
        id=record.id,
        query=record.query,
        headline=record.headline,
        description=getattr(record, "description", None),
        story_description=getattr(record, "story_description", None),
        hashtags=getattr(record, "hashtags", None),
        settings_snapshot=record.settings_snapshot,
        news_results=record.news_results,
        media_filename=media_filename,
        media_url=public_image_url(media_filename, http_request) if media_filename else None,
        publish_status=getattr(record, "publish_status", None) or "draft",
        published_at=record.published_at.isoformat() if getattr(record, "published_at", None) else None,
        platforms_published=platforms_published or None,
        related_published_post_id=getattr(record, "related_published_post_id", None),
        created_at=record.created_at.isoformat() if record.created_at else "",
        updated_at=record.updated_at.isoformat() if getattr(record, "updated_at", None) else None,
    )


def _history_detail_response(
    record: SocialPostHistory,
    *,
    http_request: Request | None = None,
) -> SocialPostHistoryDetailResponse:
    base = _history_item_response(record, http_request=http_request)
    pubs = list(getattr(record, "publications", []) or [])
    return SocialPostHistoryDetailResponse(
        **base.model_dump(),
        publications=[
            SocialPostPublicationResponse(
                platform=p.platform,
                status=p.status,
                external_id=p.external_id,
                external_url=p.external_url,
                error=p.error,
                published_at=p.published_at.isoformat() if p.published_at else None,
            )
            for p in pubs
        ],
    )


def _validate_remote_image_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL is required")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http(s) image URLs are allowed")
    host = (parsed.hostname or "").lower()
    if not host or host in _BLOCKED_IMAGE_HOSTS or host.endswith(".local"):
        raise ValueError("Image URL is not allowed")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Image URL is not allowed")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
    return parsed.geturl()


@router.post("/fetch-image", response_model=SocialPostFetchImageResponse)
async def fetch_social_post_image(
    request: SocialPostFetchImageRequest,
    current_user: User = Depends(get_current_user),
):
    """Fetch a remote image server-side for CORS-safe PNG export in the browser."""
    try:
        image_url = _validate_remote_image_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, verify=httpx_verify()) as client:
            response = await client.get(
                image_url,
                headers={"User-Agent": "SEO-Automation/1.0", "Accept": "image/*"},
            )
        response.raise_for_status()
        content_type = (response.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="URL did not return an image",
            )
        body = response.content
        if len(body) > MAX_FETCH_IMAGE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Image is too large to export",
            )
        encoded = base64.b64encode(body).decode("ascii")
        return SocialPostFetchImageResponse(
            data_url=f"data:{content_type};base64,{encoded}",
            content_type=content_type,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch image: {exc}",
        )


def _validate_presets_store(store: SocialPostTemplatePresetsStore) -> SocialPostTemplatePresetsStore:
    if store.version != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported template presets version",
        )
    if not store.presets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one template preset is required",
        )
    preset_ids = {p.id for p in store.presets}
    if store.activePresetId not in preset_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="activePresetId must match a preset id",
        )
    return store


@router.get("/template-presets", response_model=SocialPostTemplatePresetsResponse)
async def get_social_post_template_presets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get saved brand template presets for the current user."""
    row = (
        db.query(SocialPostTemplatePrefs)
        .filter(SocialPostTemplatePrefs.user_id == current_user.id)
        .first()
    )
    if not row:
        return SocialPostTemplatePresetsResponse(store=None, updated_at=None)
    try:
        store = SocialPostTemplatePresetsStore.model_validate(row.store)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored template presets are invalid: {exc}",
        )
    return SocialPostTemplatePresetsResponse(
        store=store,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.put("/template-presets", response_model=SocialPostTemplatePresetsResponse)
async def save_social_post_template_presets(
    request: SocialPostTemplatePresetsSaveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save brand template presets for the current user (synced across devices)."""
    store = _validate_presets_store(request.store)
    payload = store.model_dump()

    row = (
        db.query(SocialPostTemplatePrefs)
        .filter(SocialPostTemplatePrefs.user_id == current_user.id)
        .first()
    )
    try:
        if row:
            row.store = payload
        else:
            row = SocialPostTemplatePrefs(user_id=current_user.id, store=payload)
            db.add(row)
        db.commit()
        db.refresh(row)
        return SocialPostTemplatePresetsResponse(
            store=store,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saving template presets: {exc}",
        )



async def _resolve_article_image(
    site, wp_post: dict, content_html: str, title: str,
    target_keyword, serp_key,
    *,
    db=None,
    user_id=None,
) -> tuple[str | None, str | None]:
    """Featured media, else a body image, else a keyword image search.

    Each step is best-effort: an article with no image must still generate a
    post, because the copy is the point and the background can be set by hand.
    Prefer Custom Image Search when configured; otherwise SerpAPI.
    """
    featured_id = wp_post.get("featured_media")
    if featured_id:
        try:
            media = await get_wp_media(site, int(featured_id))
            if media:
                return media, "featured"
        except Exception as exc:
            logger.info("featured media lookup failed: %s", exc)

    body_image = article_social_service.first_body_image(content_html)
    if body_image:
        return body_image, "body"

    keyword = article_social_service.image_keyword(title, target_keyword)
    if keyword and db is not None and user_id is not None:
        try:
            results = await search_social_images(
                db,
                user_id,
                keyword,
                num_results=10,
                serpapi_key=serp_key,
            )
            for item in results or []:
                url = item.get("original") or item.get("thumbnail")
                if url:
                    return url, "search"
        except Exception as exc:
            logger.info("image search failed for %r: %s", keyword, exc)
    return None, None


async def _bundle_from_article(db, current_user, request, serp_key):
    """Build the post from the user's own article rather than the news."""
    row = None
    if request.published_post_id:
        row = (
            db.query(PublishedPost)
            .filter(
                PublishedPost.id == request.published_post_id,
                PublishedPost.user_id == current_user.id,
            )
            .first()
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That published article was not found.",
            )
        site = row.wordpress_site
        wp_post_id = row.wp_post_id
    elif request.wordpress_site_id and request.wp_post_id:
        # An article published outside this app: no published_posts row, so it
        # is addressed by site + WordPress post id instead.
        site = (
            db.query(WordPressSite)
            .filter(
                WordPressSite.id == request.wordpress_site_id,
                WordPressSite.user_id == current_user.id,
            )
            .first()
        )
        if not site:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That website was not found.",
            )
        wp_post_id = request.wp_post_id
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select a published article.",
        )

    wp_post = await get_wp_post(site, wp_post_id)
    if not wp_post.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not read that article from WordPress.",
        )

    title = wp_post.get("title") or (row.title if row else "") or ""
    content_html = wp_post.get("content") or ""
    item = article_social_service.as_news_result(
        title=title,
        content_html=content_html,
        url=(row.canonical_url or row.wp_link) if row else wp_post.get("link"),
        site=getattr(site, "name", None),
    )
    if not item["title"] and not item["snippet"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That article has no readable content to write from.",
        )

    bundle = await build_bundle_from_items(
        [item],
        query=title,
        template_variant=request.template_variant,
    )
    image_url, image_source = await _resolve_article_image(
        site, wp_post, content_html, title,
        row.target_keyword if row else None, serp_key,
        db=db,
        user_id=current_user.id,
    )
    return bundle, image_url, image_source


@router.post("/generate", response_model=SocialPostGenerateResponse)
async def generate_social_post(
    request: SocialPostGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate a social post headline from a query: fetch 7 Google News results,
    then use the LLM to produce a single compelling headline.
    """
    llm_token = None
    image_url = None
    image_source = None
    try:
        creds = resolve_llm_credentials(db, current_user.id)
        llm_token = set_llm_context(creds)

        if request.source == "article":
            serp_key = resolve_api_key_optional(db, current_user, "serpapi")
            bundle, image_url, image_source = await _bundle_from_article(
                db, current_user, request, serp_key
            )
        else:
            serp_key = resolve_api_key(db, current_user, "serpapi")
            query = (request.query or "").strip()
            if not query:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Enter a topic or search query.",
                )
            bundle = await build_news_bundle(
                query,
                template_variant=request.template_variant,
                serpapi_api_key=serp_key,
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
            image_url=image_url,
            image_source=image_source,
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
    finally:
        if llm_token is not None:
            reset_llm_context(llm_token)


@router.get("/history", response_model=list[SocialPostHistoryItemResponse])
async def get_social_post_history(
    http_request: Request,
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
        history_ids = [r.id for r in rows]
        pubs_by_history: dict[int, list[SocialPostPublication]] = {}
        if history_ids:
            pub_rows = (
                db.query(SocialPostPublication)
                .filter(SocialPostPublication.history_id.in_(history_ids))
                .all()
            )
            for pub in pub_rows:
                pubs_by_history.setdefault(pub.history_id, []).append(pub)
        return [
            _history_item_response(r, publications=pubs_by_history.get(r.id, []), http_request=http_request)
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
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a generated social post to history (for later preview and download)."""
    try:
        if request.related_published_post_id is not None:
            related_post = db.query(PublishedPost).filter(
                PublishedPost.id == request.related_published_post_id,
                PublishedPost.user_id == current_user.id,
                PublishedPost.wp_status == "publish",
            ).first()
            if not related_post:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Related published blog not found",
                )
        record = SocialPostHistory(
            user_id=current_user.id,
            query=request.query,
            headline=request.headline,
            description=request.description,
            story_description=request.story_description,
            hashtags=request.hashtags,
            settings_snapshot=request.settings_snapshot,
            news_results=request.news_results,
            related_published_post_id=request.related_published_post_id,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return _history_item_response(record, http_request=http_request)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saving social post to history: {str(e)}",
        )


@router.get("/history/{history_id}", response_model=SocialPostHistoryDetailResponse)
async def get_social_post_history_item(
    history_id: int,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        history = load_history_post(db, current_user.id, history_id)
        pubs = (
            db.query(SocialPostPublication)
            .filter(SocialPostPublication.history_id == history_id)
            .all()
        )
        history.publications = pubs
        return _history_detail_response(history, http_request=http_request)
    except HistoryNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Social post not found")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching social post history item: {str(e)}",
        )


@router.delete("/history/{history_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_social_post_history_item(
    history_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a saved social post, its publication records, and its rendered card."""
    try:
        history = load_history_post(db, current_user.id, history_id)
    except HistoryNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Social post not found")

    media_filename = history.media_filename
    try:
        db.delete(history)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting social post: {str(e)}",
        )

    # The row is gone either way; a stale file on disk must not fail the request.
    if media_filename:
        try:
            resolve_media_path(media_filename).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove social post media %s", media_filename, exc_info=True)

    return None


@router.post("/history/{history_id}/media", response_model=SocialPostImageUploadResponse)
async def save_social_post_history_media(
    history_id: int,
    request: SocialPostHistoryMediaRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        history = load_history_post(db, current_user.id, history_id)
        filename, url = ensure_media_saved(db, history, request.image_base64, request=http_request)
        return SocialPostImageUploadResponse(filename=filename, url=url)
    except HistoryNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Social post not found")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error saving media: {str(e)}",
        )


@router.post("/history/{history_id}/publish", response_model=SocialPostPublishResponse)
async def publish_social_post_from_history(
    history_id: int,
    request: SocialPostHistoryPublishRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not request.platforms:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least one platform")
    try:
        results, history, media_url = await publish_history_post(
            db,
            current_user.id,
            history_id,
            request.platforms,
            image_base64=request.image_base64,
            platform_options=request.platform_options,
            request=http_request,
        )
        return SocialPostPublishResponse(
            results=results,
            history_status=history.publish_status,
            media_url=media_url,
        )
    except HistoryNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Social post not found")
    except MediaRequiredError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error publishing social post: {str(e)}",
        )


@router.post("/upload-image", response_model=SocialPostImageUploadResponse)
async def upload_social_post_image(
    request: SocialPostImageUploadRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
):
    try:
        filename = save_png_base64(request.image_base64)
        return SocialPostImageUploadResponse(
            filename=filename,
            url=public_image_url(filename, http_request),
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


@router.post("/publish", response_model=SocialPostPublishResponse, deprecated=True)
async def publish_social_post_legacy(
    request: SocialPostPublishRequest,
    current_user: User = Depends(get_current_user),
):
    """Deprecated: save to history and use POST /history/{history_id}/publish."""
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Use POST /api/social-post/history/{history_id}/publish instead. Save the post to history first.",
    )
