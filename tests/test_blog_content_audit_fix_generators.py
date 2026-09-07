"""Tests for blog content audit fix generators."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.blog_content_audit_fix_generators import (
    append_sources_footer,
    fix_image_alt_html,
    generate_fix_proposal,
)
from app.services.blog_content_audit_fix_planner import FixPlanItem


def test_append_sources_footer():
    html = "<p>Body content here.</p>"
    after = append_sources_footer(html)
    assert "Last Updated" in after
    assert "Wikipedia" in after
    assert html in after


def test_fix_image_alt_html():
    html = '<p>Text</p><img src="/a.jpg" /><img src="/b.jpg" alt="ok" />'
    fixed = fix_image_alt_html(html, max_images=5)
    assert 'alt="a"' in fixed or 'alt="Article illustration"' in fixed


@pytest.mark.asyncio
async def test_generate_meta_fix():
    plan = FixPlanItem(
        fix_type="add_meta_description",
        target_field="meta",
        title="Meta",
        description="",
        severity="high",
    )
    with patch(
        "app.services.blog_content_audit_fix_generators.generate_content",
        new=AsyncMock(return_value="A compelling meta description for SEO purposes here."),
    ):
        result = await generate_fix_proposal(
            plan,
            title="Jane Doe Net Worth",
            article_type="net_worth",
            content_html="<p>Content</p>",
            meta_description=None,
        )
    assert result["after_proposed"]
    assert len(result["diff_segments"]) >= 1
