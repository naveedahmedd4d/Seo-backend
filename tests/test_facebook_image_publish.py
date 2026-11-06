"""Focused tests for publishing saved image cards to Facebook Pages."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.facebook_service import publish_page_photo
from app.services.social_post_copy_service import PublishCopyContext
from app.services.social_post_platform_publish import publish_to_platform
from app.services.social_post_publish_service import publish_to_platforms


@pytest.mark.asyncio
@patch("app.services.facebook_service.graph_post_multipart", new_callable=AsyncMock)
async def test_publish_page_photo_uploads_local_png_bytes(mock_graph_post):
    mock_graph_post.return_value = {"id": "photo-1", "post_id": "page-1_post-1"}
    image = b"\x89PNG\r\n\x1a\nlocal-image"

    result = await publish_page_photo(
        "page-token",
        page_id="page-1",
        caption="Story caption",
        image_bytes=image,
    )

    assert result["post_id"] == "page-1_post-1"
    mock_graph_post.assert_awaited_once_with(
        "page-1/photos",
        "page-token",
        data={"caption": "Story caption", "published": "true"},
        files={"source": ("social-post.png", image, "image/png")},
    )


@pytest.mark.asyncio
@patch("app.services.social_post_platform_publish.publish_page_photo", new_callable=AsyncMock)
@patch("app.services.social_post_platform_publish.get_valid_facebook_page_context", new_callable=AsyncMock)
@patch("app.services.social_post_platform_publish.get_user_social_account")
async def test_facebook_publish_uses_saved_bytes_and_keeps_source_link(
    mock_get_account,
    mock_get_page,
    mock_publish_photo,
):
    mock_get_account.return_value = object()
    mock_get_page.return_value = ("page-1", "TrendifyPress", "page-token")
    mock_publish_photo.return_value = {"post_id": "page-1_post-1"}
    image = b"\x89PNG\r\n\x1a\nlocal-image"
    adapted = b"\xff\xd8\xffadapted-jpeg"
    ctx = PublishCopyContext(
        headline="Headline",
        short_description="Description",
        story_caption="Story caption",
        hashtags=["news", "#trending"],
        source_url="https://example.com/article",
    )

    with patch(
        "app.services.social_post_platform_publish.upload_post_configured",
        return_value=False,
    ), patch(
        "app.services.social_post_platform_publish.adapt_social_card_for_facebook_feed",
        return_value=adapted,
    ):
        result = await publish_to_platform(
            object(),
            7,
            "facebook",
            ctx=ctx,
            image_url="http://localhost:8000/api/social-post/media/card.png",
            image_bytes=image,
            platform_options={"page_id": "page-1"},
        )

    assert result.success is True
    assert result.external_id == "page-1_post-1"
    mock_publish_photo.assert_awaited_once_with(
        "page-token",
        page_id="page-1",
        caption="Headline\n\nStory caption\n\n#news #trending\n\nhttps://example.com/article",
        image_bytes=adapted,
    )


@pytest.mark.asyncio
@patch("app.services.social_post_platform_publish.publish_page_photo", new_callable=AsyncMock)
@patch("app.services.social_post_platform_publish.get_valid_facebook_page_context", new_callable=AsyncMock)
@patch("app.services.social_post_platform_publish.get_user_social_account")
async def test_facebook_publish_downloads_remote_image_when_local_bytes_are_absent(
    mock_get_account,
    mock_get_page,
    mock_publish_photo,
):
    mock_get_account.return_value = object()
    mock_get_page.return_value = ("page-1", "TrendifyPress", "page-token")
    mock_publish_photo.return_value = {"id": "photo-1"}
    ctx = PublishCopyContext(
        headline="Headline",
        short_description=None,
        story_caption=None,
        hashtags=[],
        source_url=None,
    )

    remote_bytes = b"\x89PNG\r\n\x1a\nremote-image"
    adapted = b"\xff\xd8\xffremote-adapted"

    with patch(
        "app.services.social_post_platform_publish.upload_post_configured",
        return_value=False,
    ), patch(
        "app.services.social_post_platform_publish.download_image_bytes",
        new_callable=AsyncMock,
        return_value=remote_bytes,
    ) as mock_download, patch(
        "app.services.social_post_platform_publish.adapt_social_card_for_facebook_feed",
        return_value=adapted,
    ):
        result = await publish_to_platform(
            object(),
            7,
            "facebook",
            ctx=ctx,
            image_url="https://cdn.example.com/card.png",
            image_bytes=None,
            platform_options={},
        )

    assert result.success is True
    mock_download.assert_awaited_once_with("https://cdn.example.com/card.png")
    mock_publish_photo.assert_awaited_once()
    assert mock_publish_photo.await_args.kwargs["image_bytes"] == adapted


@pytest.mark.asyncio
@patch("app.services.social_post_platform_publish.publish_page_photo", new_callable=AsyncMock)
@patch("app.services.social_post_platform_publish.get_valid_facebook_page_context", new_callable=AsyncMock)
@patch("app.services.social_post_platform_publish.publish_photo_via_upload_post", new_callable=AsyncMock)
@patch("app.services.social_post_platform_publish.upload_post_configured", return_value=True)
@patch("app.services.social_post_platform_publish.supports_upload_post", return_value=True)
@patch("app.services.social_post_platform_publish.get_user_social_account")
async def test_facebook_falls_through_to_oauth_when_upload_post_fails(
    mock_get_account,
    _supports,
    _configured,
    mock_upload_post,
    mock_get_page,
    mock_publish_photo,
):
    """Connected Facebook OAuth must still publish when Upload-Post returns a non-auth error."""
    from app.schemas.social_post import SocialPostPublishResultItem

    mock_get_account.return_value = object()
    mock_upload_post.return_value = SocialPostPublishResultItem(
        platform="facebook",
        success=False,
        error="Failed to download photo from URL",
    )
    mock_get_page.return_value = ("page-1", "Fact Check", "page-token")
    mock_publish_photo.return_value = {"post_id": "page-1_post-99"}
    image = b"\x89PNG\r\n\x1a\nlocal-image"
    adapted = b"\xff\xd8\xffadapted"
    ctx = PublishCopyContext(
        headline="Headline",
        short_description=None,
        story_caption="Caption",
        hashtags=[],
        source_url=None,
    )

    with patch(
        "app.services.social_post_platform_publish.adapt_social_card_for_facebook_feed",
        return_value=adapted,
    ):
        result = await publish_to_platform(
            object(),
            7,
            "facebook",
            ctx=ctx,
            image_url="https://seo-backend.example/media/x.png",
            image_bytes=image,
            platform_options={},
        )

    assert result.success is True
    assert result.external_id == "page-1_post-99"
    mock_publish_photo.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.services.social_post_publish_service.publish_to_platform", new_callable=AsyncMock)
@patch(
    "app.services.social_post_publish_service.resolve_facebook_destination_url",
    new_callable=AsyncMock,
    return_value="https://example.com/owned-article",
)
async def test_history_publisher_reads_saved_file_for_facebook(mock_resolve_url, mock_publish):
    image = b"\x89PNG\r\n\x1a\nsaved-local-image"
    saved_path = MagicMock()
    path_reader = MagicMock()
    path_reader.return_value.read_bytes.return_value = image
    history = SimpleNamespace(
        media_filename="card.png",
        headline="Headline",
        description="Description",
        story_description="Story caption",
        hashtags=[],
        news_results=[{"link": "https://example.com/article"}],
    )
    mock_publish.return_value = SimpleNamespace(platform="facebook", success=True)

    with patch(
        "app.services.social_post_publish_service.resolve_media_path",
        return_value=saved_path,
        ), patch(
            "app.services.social_post_publish_service.Path",
            path_reader,
        ):
        results = await publish_to_platforms(
            object(),
            7,
            history,
            ["facebook"],
        )

    assert results[0].success is True
    assert mock_publish.await_args.kwargs["image_bytes"] == image
    assert mock_publish.await_args.kwargs["ctx"].source_url == "https://example.com/owned-article"
    mock_resolve_url.assert_awaited_once()
