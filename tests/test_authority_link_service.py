"""Tests for authority_link_service."""

from app.models.authority_site import (
    AuthorityLinkEdge,
    AuthorityLinkStatus,
    AuthorityLinkType,
    AuthorityNodeStatus,
    AuthorityServiceCluster,
    AuthoritySite,
)
from app.services.authority_link_service import (
    build_slug_map,
    inject_links_into_markdown,
    resolve_href,
)


def _make_site() -> AuthoritySite:
    site = AuthoritySite(
        id=1,
        user_id=1,
        wordpress_site_id=1,
        money_page_title="Money",
        money_page_slug="money-page",
        money_page_focus_keyword="money kw",
        money_page_status=AuthorityNodeStatus.PLANNED,
    )
    cluster = AuthorityServiceCluster(
        id=10,
        authority_site_id=1,
        title="Service A",
        slug="service-a",
        focus_keyword="service a",
        status=AuthorityNodeStatus.PLANNED,
        color="blue",
        sort_order=0,
    )
    cluster.blog_posts = []
    site.service_clusters = [cluster]
    return site


def test_build_slug_map():
    site = _make_site()
    slug_map = build_slug_map(site)
    assert slug_map["money:1"] == "/money-page"
    assert slug_map["service:10"] == "/service-a"


def test_resolve_href():
    slug_map = {"service:10": "/service-a"}
    assert resolve_href(slug_map, "service", 10) == "/service-a"
    assert resolve_href(slug_map, "blog", 99) == "#"


def test_inject_links_appends_related_block():
    site = _make_site()
    slug_map = build_slug_map(site)
    edges = [
        AuthorityLinkEdge(
            id=1,
            authority_site_id=1,
            source_type="service",
            source_id=10,
            target_type="money",
            target_id=1,
            link_type=AuthorityLinkType.SERVICE_TO_MONEY,
            anchor_text="our main page",
            status=AuthorityLinkStatus.PLANNED,
        )
    ]
    body = "# Service page\n\nContent here."
    result = inject_links_into_markdown(
        body, edges, slug_map, source_type="service", source_id=10
    )
    assert "[our main page](/money-page)" in result
    assert "## Related pages" in result
