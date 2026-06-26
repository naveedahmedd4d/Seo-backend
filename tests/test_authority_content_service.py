"""Tests for authority_content_service prompt assembly."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.authority_site import (
    AuthorityBlogPost,
    AuthorityNodeStatus,
    AuthorityServiceCluster,
    AuthoritySite,
)
from app.models.wordpress_site import WordPressSite
from app.services.authority_content_service import (
    generate_blog_content,
    generate_money_page_content,
    generate_service_page_content,
)


def _site_with_cluster() -> tuple[AuthoritySite, AuthorityServiceCluster, AuthorityBlogPost]:
    wp = WordPressSite(id=1, user_id=1, name="Acme Video", url="https://acme.com", api_key="x", username="u")
    site = AuthoritySite(
        id=1,
        user_id=1,
        wordpress_site_id=1,
        money_page_title="Best Video Editing Delhi",
        money_page_slug="video-editing-delhi",
        money_page_focus_keyword="best video editing delhi",
        money_page_status=AuthorityNodeStatus.PLANNED,
        wordpress_site=wp,
    )
    cluster = AuthorityServiceCluster(
        id=2,
        authority_site_id=1,
        title="Wedding Video Editing",
        slug="wedding-video-editing",
        focus_keyword="wedding video editing",
        status=AuthorityNodeStatus.PLANNED,
        color="blue",
        sort_order=0,
    )
    blog = AuthorityBlogPost(
        id=3,
        cluster_id=2,
        title="Wedding video editing cost",
        slug="wedding-video-editing-cost",
        focus_keyword="wedding video editing cost",
        article_type="cost_guide",
        status=AuthorityNodeStatus.PLANNED,
        sort_order=0,
    )
    cluster.blog_posts = [blog]
    site.service_clusters = [cluster]
    site.link_edges = []
    return site, cluster, blog


@pytest.mark.asyncio
async def test_money_page_prompt_includes_services():
    site, _, _ = _site_with_cluster()
    with patch(
        "app.services.authority_content_service.generate_blog_seo_content",
        new_callable=AsyncMock,
    ) as mock_llm:
        mock_llm.return_value = "# Money page body"
        with patch(
            "app.services.authority_content_service.generate_seo_title",
            new_callable=AsyncMock,
            return_value="SEO Title",
        ), patch(
            "app.services.authority_content_service.generate_meta_description",
            new_callable=AsyncMock,
            return_value="Meta desc",
        ):
            result = await generate_money_page_content(site)
        prompt = mock_llm.call_args[0][0]
        assert "MONEY PAGE" in prompt
        assert "Wedding Video Editing" in prompt
        assert result["body"] == "# Money page body"


@pytest.mark.asyncio
async def test_blog_prompt_uses_article_type_hint():
    site, cluster, blog = _site_with_cluster()
    with patch(
        "app.services.authority_content_service.generate_blog_seo_content",
        new_callable=AsyncMock,
    ) as mock_llm:
        mock_llm.return_value = "# Blog body"
        with patch(
            "app.services.authority_content_service.generate_seo_title",
            new_callable=AsyncMock,
            return_value="Blog SEO",
        ), patch(
            "app.services.authority_content_service.generate_meta_description",
            new_callable=AsyncMock,
            return_value="Blog meta",
        ):
            await generate_blog_content(site, cluster, blog)
        prompt = mock_llm.call_args[0][0]
        assert "cost breakdown" in prompt.lower()
        assert "wedding-video-editing" in prompt


@pytest.mark.asyncio
async def test_service_page_prompt_includes_money_keyword():
    site, cluster, _ = _site_with_cluster()
    with patch(
        "app.services.authority_content_service.generate_blog_seo_content",
        new_callable=AsyncMock,
    ) as mock_llm:
        mock_llm.return_value = "# Service body"
        with patch(
            "app.services.authority_content_service.generate_seo_title",
            new_callable=AsyncMock,
            return_value="Service SEO",
        ), patch(
            "app.services.authority_content_service.generate_meta_description",
            new_callable=AsyncMock,
            return_value="Service meta",
        ):
            await generate_service_page_content(site, cluster)
        prompt = mock_llm.call_args[0][0]
        assert "SERVICE PAGE" in prompt
        assert site.money_page_focus_keyword in prompt
