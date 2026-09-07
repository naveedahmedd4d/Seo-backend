"""Cluster hub links, orchestration skip logic, and member auto-images."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.models.workflow_state import WorkflowStatus
from app.schemas.blog import BlogAutoImagesRequest
from app.services.blog_image_service import (
    apply_auto_images_to_workflow,
    insert_image_after_section,
)
from app.services.cluster_hub_links import (
    content_types_to_generate,
    fallback_member_path,
    inject_cluster_hub_member_links,
    prefer_member_link_path,
)


def test_content_types_to_generate_skips_linked_net_worth():
    assert content_types_to_generate("net_worth", None, 99) == []
    assert content_types_to_generate("net_worth", None, None) == ["net_worth"]
    assert content_types_to_generate("biography", 12, None) == []
    assert content_types_to_generate("both", 1, None) == ["net_worth"]
    assert content_types_to_generate("both", None, 2) == ["biography"]
    assert content_types_to_generate("both", 1, 2) == []


def test_prefer_member_link_path_prefers_net_worth():
    assert prefer_member_link_path(
        net_worth_path="/caitlin-clark-net-worth",
        biography_path="/caitlin-clark",
    ) == "/caitlin-clark-net-worth"
    assert prefer_member_link_path(biography_path="caitlin-clark") == "/caitlin-clark"
    assert prefer_member_link_path() is None


def test_fallback_member_path():
    assert fallback_member_path("Caitlin Clark", "net_worth") == "/caitlin-clark-net-worth"
    assert fallback_member_path("Caitlin Clark", "biography") == "/caitlin-clark"


def test_inject_cluster_hub_member_links_under_h3():
    body = (
        "## The List\n\n"
        "Overview.\n\n"
        "### Caitlin Clark\n\n"
        "Guard for Indiana.\n\n"
        "### Angel Reese\n\n"
        "Forward for Chicago.\n\n"
        "## Looking Ahead\n\n"
        "Close."
    )
    result = inject_cluster_hub_member_links(
        body,
        [
            {"name": "Caitlin Clark", "path": "/caitlin-clark-net-worth"},
            {"name": "Angel Reese", "path": "/angel-reese-net-worth"},
        ],
    )
    assert "[Full net worth profile](/caitlin-clark-net-worth)" in result
    assert "[Full net worth profile](/angel-reese-net-worth)" in result
    assert result.index("### Caitlin Clark") < result.index("/caitlin-clark-net-worth")
    assert result.index("/caitlin-clark-net-worth") < result.index("### Angel Reese")


def test_inject_skips_missing_map_no_fake_link():
    body = "### Caitlin Clark\n\nText.\n"
    result = inject_cluster_hub_member_links(body, [{"name": "Someone Else", "path": "/x"}])
    assert "/caitlin" not in result
    assert result == body


def test_inject_idempotent():
    body = "### Caitlin Clark\n\nText.\n"
    once = inject_cluster_hub_member_links(
        body, [{"name": "Caitlin Clark", "path": "/caitlin-clark-net-worth"}]
    )
    twice = inject_cluster_hub_member_links(
        once, [{"name": "Caitlin Clark", "path": "/caitlin-clark-net-worth"}]
    )
    assert once.count("/caitlin-clark-net-worth") == 1
    assert twice.count("/caitlin-clark-net-worth") == 1


def test_insert_image_after_h3_member_heading():
    body = (
        "## The List\n\n"
        "Intro.\n\n"
        "### Caitlin Clark\n\n"
        "She plays for Indiana.\n\n"
        "### Angel Reese\n\n"
        "Chicago forward."
    )
    result = insert_image_after_section(
        body,
        ["Caitlin Clark"],
        "https://example.com/cc.jpg",
        "Caitlin Clark",
    )
    assert "https://example.com/cc.jpg" in result
    assert result.index("### Caitlin Clark") < result.index("https://example.com/cc.jpg")
    assert result.index("https://example.com/cc.jpg") < result.index("### Angel Reese")


def test_blog_auto_images_request_allows_cluster_hub():
    req = BlogAutoImagesRequest(content_type="cluster_hub")
    assert req.content_type == "cluster_hub"


def test_blog_auto_images_request_still_rejects_unknown():
    with pytest.raises(ValidationError):
        BlogAutoImagesRequest(content_type="religion")  # type: ignore[arg-type]


def _make_workflow(state_data: dict, workflow_id: int = 1) -> MagicMock:
    wf = MagicMock()
    wf.id = workflow_id
    wf.status = WorkflowStatus.COMPLETED
    wf.state_data = state_data
    return wf


@pytest.mark.asyncio
async def test_apply_cluster_hub_auto_images_per_member(monkeypatch):
    hub_body = (
        "## Introduction\n\nIntro.\n\n"
        "## The List\n\n"
        "### Caitlin Clark\n\n"
        "Text about Clark.\n\n"
        "### Angel Reese\n\n"
        "Text about Reese.\n\n"
        "## Looking Ahead\n\nClose."
    )
    wf = _make_workflow(
        {
            "title": "WNBA Net Worth 2026",
            "body_content": hub_body,
            "focus_keyword": "WNBA Net Worth 2026",
            "cluster_subs": ["Caitlin Clark", "Angel Reese"],
            "content_intent": "cluster_hub",
        }
    )
    db = MagicMock()

    featured = {
        "url": "https://img/featured.jpg",
        "alt": "Featured",
        "thumbnail": "https://img/featured.jpg",
    }
    clark = {
        "url": "https://img/clark.jpg",
        "alt": "Caitlin Clark",
        "thumbnail": "https://img/clark.jpg",
    }
    reese = {
        "url": "https://img/reese.jpg",
        "alt": "Angel Reese",
        "thumbnail": "https://img/reese.jpg",
    }

    async def fake_pool(_db, _uid, keyword):
        if "Caitlin" in keyword:
            return [{"original": clark["url"], "link": clark["url"], "title": "Clark"}]
        if "Angel" in keyword:
            return [{"original": reese["url"], "link": reese["url"], "title": "Reese"}]
        return [{"original": featured["url"], "link": featured["url"], "title": "Feat"}]

    picks = [featured, clark, reese]

    async def fake_pick(pool, used_urls, context_title, meta="", pick_slot=0):
        if not picks:
            return None
        return picks.pop(0)

    monkeypatch.setattr(
        "app.services.blog_image_service._fetch_image_pool",
        fake_pool,
    )
    monkeypatch.setattr(
        "app.services.blog_image_service.pick_from_pool",
        fake_pick,
    )
    # Avoid real HTTP verification if pick_from_pool path changes
    monkeypatch.setattr(
        "app.services.blog_image_service.resolve_verified_url",
        AsyncMock(side_effect=lambda u, **kw: u),
    )

    result = await apply_auto_images_to_workflow(
        db,
        wf,
        user_id=1,
        content_type="cluster_hub",
        used_urls=set(),
    )

    body = wf.state_data["body_content"]
    assert "https://img/clark.jpg" in body
    assert "https://img/reese.jpg" in body
    assert result["featured_image"] is not None
    assert not any("career" in w.lower() or "personal_life" in w.lower() for w in result["warnings"])
    assert len(result["section_images"]) == 2
