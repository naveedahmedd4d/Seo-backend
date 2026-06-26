"""Authority Builder internal link injection — separate from celebrity blog tree."""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.authority_site import (
    AuthorityBlogPost,
    AuthorityLinkEdge,
    AuthorityLinkStatus,
    AuthorityLinkType,
    AuthorityServiceCluster,
    AuthoritySite,
)


def build_slug_map(site: AuthoritySite) -> Dict[str, str]:
    """Map node keys to URL paths (leading slash)."""
    slug_map: Dict[str, str] = {
        f"money:{site.id}": f"/{site.money_page_slug}",
    }
    for cluster in site.service_clusters:
        slug_map[f"service:{cluster.id}"] = f"/{cluster.slug}"
        for blog in cluster.blog_posts:
            slug_map[f"blog:{blog.id}"] = f"/{blog.slug}"
    return slug_map


def resolve_href(slug_map: Dict[str, str], target_type: str, target_id: int) -> str:
    key = f"{target_type}:{target_id}"
    return slug_map.get(key, "#")


def get_outbound_edges(
    edges: List[AuthorityLinkEdge],
    source_type: str,
    source_id: int,
) -> List[AuthorityLinkEdge]:
    return [
        e
        for e in edges
        if e.source_type == source_type and e.source_id == source_id
    ]


def _md_link(anchor: str, href: str) -> str:
    a = (anchor or "read more").strip()
    h = href if href.startswith("/") else f"/{href.lstrip('/')}"
    return f"[{a}]({h})"


def inject_links_into_markdown(
    body: str,
    edges: List[AuthorityLinkEdge],
    slug_map: Dict[str, str],
    *,
    source_type: str,
    source_id: int,
) -> str:
    """Inject contextual links + related block for authority nodes."""
    content = (body or "").strip()
    outbound = get_outbound_edges(edges, source_type, source_id)
    if not outbound:
        return content

    related_items: List[str] = []
    used_targets: set[Tuple[str, int]] = set()

    for edge in outbound:
        target_key = (edge.target_type, edge.target_id)
        if target_key in used_targets:
            continue
        href = resolve_href(slug_map, edge.target_type, edge.target_id)
        anchor = (edge.anchor_text or "read more").strip()
        related_items.append(_md_link(anchor, href))
        used_targets.add(target_key)

        if edge.link_type == AuthorityLinkType.BLOG_TO_SERVICE and content:
            phrase = anchor.split()[0] if anchor else ""
            if phrase and phrase.lower() in content.lower() and f"]({href})" not in content:
                idx = content.lower().find(phrase.lower())
                if idx >= 0:
                    end = idx + len(phrase)
                    content = content[:end] + f" ({_md_link(anchor, href)})" + content[end:]

    if related_items:
        block_title = "## Related guides" if source_type == "blog" else "## Related pages"
        links_line = "\n".join(f"- {item}" for item in related_items)
        content = f"{content}\n\n{block_title}\n{links_line}"

    return content


def append_schema_script(content_html: str, schema: Optional[Dict[str, Any]]) -> str:
    if not schema:
        return content_html
    import json

    return (
        content_html
        + f'\n<script type="application/ld+json">\n{json.dumps(schema)}\n</script>'
    )


def append_faqs_html(content_html: str, faqs: List[Dict[str, str]]) -> str:
    if not faqs:
        return content_html
    items = []
    for faq in faqs:
        q = html.escape((faq.get("question") or "").strip())
        a = html.escape((faq.get("answer") or "").strip())
        if q and a:
            items.append(f"<h3>{q}</h3>\n<p>{a}</p>")
    if not items:
        return content_html
    return content_html + "\n\n<h2 id=\"faqs\">Frequently asked questions</h2>\n" + "\n".join(items)


def mark_edges_for_node(
    db: Session,
    site_id: int,
    source_type: str,
    source_id: int,
    status: AuthorityLinkStatus,
) -> None:
    edges = (
        db.query(AuthorityLinkEdge)
        .filter(
            AuthorityLinkEdge.authority_site_id == site_id,
            AuthorityLinkEdge.source_type == source_type,
            AuthorityLinkEdge.source_id == source_id,
        )
        .all()
    )
    for edge in edges:
        edge.status = status
    db.commit()


def find_cluster_for_blog(site: AuthoritySite, blog_id: int) -> Optional[AuthorityServiceCluster]:
    for cluster in site.service_clusters:
        if any(b.id == blog_id for b in cluster.blog_posts):
            return cluster
    return None


def find_blog(site: AuthoritySite, blog_id: int) -> Optional[AuthorityBlogPost]:
    for cluster in site.service_clusters:
        for blog in cluster.blog_posts:
            if blog.id == blog_id:
                return blog
    return None
