"""Authority Builder WordPress publish orchestration."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.authority_site import (
    AuthorityBlogPost,
    AuthorityLinkStatus,
    AuthorityNodeStatus,
    AuthorityPublishedNode,
    AuthorityServiceCluster,
    AuthoritySite,
)
from app.services.authority_link_service import (
    append_faqs_html,
    append_schema_script,
    build_slug_map,
    inject_links_into_markdown,
    mark_edges_for_node,
)
from app.services.authority_service import get_authority_site, _get_blog, _get_cluster
from app.services.openrouter_service import build_faq_schema, generate_faqs
from app.services.wordpress_service import (
    delete_page_from_wordpress,
    delete_post_from_wordpress,
    get_wordpress_site,
    publish_page_to_wordpress,
    publish_to_wordpress,
    _markdown_to_html,
)

logger = logging.getLogger(__name__)


def get_recommended_publish_order(site: AuthoritySite) -> List[Tuple[str, int]]:
    """Return (node_type, node_id) in publish order: money → services → blogs."""
    order: List[Tuple[str, int]] = [("money", site.id)]
    for cluster in sorted(site.service_clusters, key=lambda c: c.sort_order):
        order.append(("service", cluster.id))
        for blog in sorted(cluster.blog_posts, key=lambda b: b.sort_order):
            order.append(("blog", blog.id))
    return order


def _get_published_record(
    db: Session, node_type: str, node_id: int, wordpress_site_id: int
) -> Optional[AuthorityPublishedNode]:
    return (
        db.query(AuthorityPublishedNode)
        .filter(
            AuthorityPublishedNode.node_type == node_type,
            AuthorityPublishedNode.node_id == node_id,
            AuthorityPublishedNode.wordpress_site_id == wordpress_site_id,
        )
        .first()
    )


async def _build_faqs_and_schema(
    title: str, keyword: str, body: str, schema_type: str
) -> Tuple[List[Dict[str, str]], Optional[Dict[str, Any]]]:
    faqs = await generate_faqs(title, keywords=[keyword], category="Lifestyle & Misc")
    schema: Optional[Dict[str, Any]] = None
    if faqs:
        faq_schema = build_faq_schema(faqs)
        if faq_schema:
            schema = faq_schema
        elif schema_type == "Article":
            schema = {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": title,
                "description": keyword,
            }
        elif schema_type == "Service":
            schema = {
                "@context": "https://schema.org",
                "@type": "Service",
                "name": title,
                "description": keyword,
            }
        else:
            schema = {
                "@context": "https://schema.org",
                "@type": "LocalBusiness",
                "name": title,
            }
    return faqs, schema


async def _assemble_money_content(site: AuthoritySite) -> str:
    slug_map = build_slug_map(site)
    edges = site.link_edges or []
    md = inject_links_into_markdown(
        site.money_page_body or "",
        edges,
        slug_map,
        source_type="money",
        source_id=site.id,
    )
    html_body = _markdown_to_html(md)
    faqs, schema = await _build_faqs_and_schema(
        site.money_page_title,
        site.money_page_focus_keyword,
        site.money_page_body or "",
        "LocalBusiness",
    )
    html_body = append_faqs_html(html_body, faqs)
    return append_schema_script(html_body, schema)


async def _assemble_service_content(site: AuthoritySite, cluster: AuthorityServiceCluster) -> str:
    slug_map = build_slug_map(site)
    edges = site.link_edges or []
    md = inject_links_into_markdown(
        cluster.body or "",
        edges,
        slug_map,
        source_type="service",
        source_id=cluster.id,
    )
    html_body = _markdown_to_html(md)
    faqs, schema = await _build_faqs_and_schema(
        cluster.title, cluster.focus_keyword, cluster.body or "", "Service"
    )
    html_body = append_faqs_html(html_body, faqs)
    return append_schema_script(html_body, schema)


async def _assemble_blog_content(site: AuthoritySite, blog: AuthorityBlogPost) -> str:
    slug_map = build_slug_map(site)
    edges = site.link_edges or []
    md = inject_links_into_markdown(
        blog.body or "",
        edges,
        slug_map,
        source_type="blog",
        source_id=blog.id,
    )
    html_body = _markdown_to_html(md)
    faqs, schema = await _build_faqs_and_schema(
        blog.title, blog.focus_keyword, blog.body or "", "Article"
    )
    html_body = append_faqs_html(html_body, faqs)
    return append_schema_script(html_body, schema)


def _upsert_published(
    db: Session,
    *,
    user_id: int,
    site: AuthoritySite,
    node_type: str,
    node_id: int,
    wp_content_type: str,
    wp_content_id: int,
    wp_link: str,
    wp_status: str,
    title: str,
) -> AuthorityPublishedNode:
    rec = _get_published_record(db, node_type, node_id, site.wordpress_site_id)
    if rec:
        rec.wp_content_id = wp_content_id
        rec.wp_link = wp_link
        rec.wp_status = wp_status
        rec.title = title
        rec.wp_content_type = wp_content_type
    else:
        rec = AuthorityPublishedNode(
            user_id=user_id,
            authority_site_id=site.id,
            wordpress_site_id=site.wordpress_site_id,
            node_type=node_type,
            node_id=node_id,
            wp_content_type=wp_content_type,
            wp_content_id=wp_content_id,
            wp_link=wp_link,
            wp_status=wp_status,
            title=title,
        )
        db.add(rec)
    db.commit()
    return rec


async def publish_money_page(
    db: Session, site_id: int, user_id: int, wp_status: str = "draft"
) -> AuthoritySite:
    site = get_authority_site(db, site_id, user_id)
    if not site.money_page_body:
        raise HTTPException(status_code=400, detail="Generate money page content first")
    wp_site = get_wordpress_site(db, site.wordpress_site_id, user_id)
    if not wp_site:
        raise HTTPException(status_code=404, detail="WordPress site not found")

    existing = _get_published_record(db, "money", site.id, site.wordpress_site_id)
    if existing:
        raise HTTPException(status_code=409, detail="Money page already published")

    content = await _assemble_money_content(site)
    result = await publish_page_to_wordpress(
        wp_site,
        site.money_page_title,
        content,
        status=wp_status,
        content_is_html=True,
        focus_keyword=site.money_page_focus_keyword,
        slug=site.money_page_slug,
        seo_title=site.money_page_seo_title,
        meta_description=site.money_page_meta_description,
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("message", "Publish failed"))

    site.money_page_wp_page_id = result.get("page_id")
    site.money_page_wp_link = result.get("link")
    site.money_page_status = AuthorityNodeStatus.PUBLISHED
    _upsert_published(
        db,
        user_id=user_id,
        site=site,
        node_type="money",
        node_id=site.id,
        wp_content_type="page",
        wp_content_id=result["page_id"],
        wp_link=result.get("link") or "",
        wp_status=wp_status,
        title=site.money_page_title,
    )
    mark_edges_for_node(db, site.id, "money", site.id, AuthorityLinkStatus.PUBLISHED)
    return get_authority_site(db, site_id, user_id)


async def publish_service_page(
    db: Session, cluster_id: int, user_id: int, wp_status: str = "draft"
) -> AuthoritySite:
    cluster = _get_cluster(db, cluster_id, user_id)
    site = get_authority_site(db, cluster.authority_site_id, user_id)
    if not cluster.body:
        raise HTTPException(status_code=400, detail="Generate service page content first")
    wp_site = get_wordpress_site(db, site.wordpress_site_id, user_id)
    if not wp_site:
        raise HTTPException(status_code=404, detail="WordPress site not found")

    existing = _get_published_record(db, "service", cluster.id, site.wordpress_site_id)
    if existing:
        raise HTTPException(status_code=409, detail="Service page already published")

    content = await _assemble_service_content(site, cluster)
    result = await publish_page_to_wordpress(
        wp_site,
        cluster.title,
        content,
        status=wp_status,
        content_is_html=True,
        focus_keyword=cluster.focus_keyword,
        slug=cluster.slug,
        seo_title=cluster.seo_title,
        meta_description=cluster.meta_description,
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("message", "Publish failed"))

    cluster.wp_page_id = result.get("page_id")
    cluster.wp_link = result.get("link")
    cluster.status = AuthorityNodeStatus.PUBLISHED
    _upsert_published(
        db,
        user_id=user_id,
        site=site,
        node_type="service",
        node_id=cluster.id,
        wp_content_type="page",
        wp_content_id=result["page_id"],
        wp_link=result.get("link") or "",
        wp_status=wp_status,
        title=cluster.title,
    )
    mark_edges_for_node(db, site.id, "service", cluster.id, AuthorityLinkStatus.PUBLISHED)
    db.commit()
    return get_authority_site(db, site.id, user_id)


async def publish_blog_post(
    db: Session, blog_id: int, user_id: int, wp_status: str = "draft"
) -> AuthoritySite:
    blog = _get_blog(db, blog_id, user_id)
    site = get_authority_site(db, blog.cluster.authority_site_id, user_id)
    if not blog.body:
        raise HTTPException(status_code=400, detail="Generate blog content first")
    wp_site = get_wordpress_site(db, site.wordpress_site_id, user_id)
    if not wp_site:
        raise HTTPException(status_code=404, detail="WordPress site not found")

    existing = _get_published_record(db, "blog", blog.id, site.wordpress_site_id)
    if existing:
        raise HTTPException(status_code=409, detail="Blog already published")

    content = await _assemble_blog_content(site, blog)
    result = await publish_to_wordpress(
        wp_site,
        blog.title,
        content,
        status=wp_status,
        content_is_html=True,
        focus_keyword=blog.focus_keyword,
        slug=blog.slug,
        seo_title=blog.seo_title,
        meta_description=blog.meta_description,
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("message", "Publish failed"))

    blog.wp_post_id = result.get("post_id")
    blog.wp_link = result.get("link")
    blog.status = AuthorityNodeStatus.PUBLISHED
    _upsert_published(
        db,
        user_id=user_id,
        site=site,
        node_type="blog",
        node_id=blog.id,
        wp_content_type="post",
        wp_content_id=result["post_id"],
        wp_link=result.get("link") or "",
        wp_status=wp_status,
        title=blog.title,
    )
    mark_edges_for_node(db, site.id, "blog", blog.id, AuthorityLinkStatus.PUBLISHED)
    db.commit()
    return get_authority_site(db, site.id, user_id)


async def publish_ready_nodes(
    db: Session, site_id: int, user_id: int, wp_status: str = "draft"
) -> AuthoritySite:
    site = get_authority_site(db, site_id, user_id)
    for node_type, node_id in get_recommended_publish_order(site):
        if node_type == "money":
            if site.money_page_body and site.money_page_status == AuthorityNodeStatus.DRAFT:
                if not _get_published_record(db, "money", site.id, site.wordpress_site_id):
                    await publish_money_page(db, site_id, user_id, wp_status)
        elif node_type == "service":
            cluster = next((c for c in site.service_clusters if c.id == node_id), None)
            if cluster and cluster.body and cluster.status == AuthorityNodeStatus.DRAFT:
                if not _get_published_record(db, "service", cluster.id, site.wordpress_site_id):
                    await publish_service_page(db, cluster.id, user_id, wp_status)
        elif node_type == "blog":
            blog = None
            for c in site.service_clusters:
                blog = next((b for b in c.blog_posts if b.id == node_id), None)
                if blog:
                    break
            if blog and blog.body and blog.status == AuthorityNodeStatus.DRAFT:
                if not _get_published_record(db, "blog", blog.id, site.wordpress_site_id):
                    await publish_blog_post(db, blog.id, user_id, wp_status)
    return get_authority_site(db, site_id, user_id)


async def unpublish_node(db: Session, published_id: int, user_id: int) -> AuthoritySite:
    rec = (
        db.query(AuthorityPublishedNode)
        .filter(AuthorityPublishedNode.id == published_id, AuthorityPublishedNode.user_id == user_id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Published record not found")

    wp_site = get_wordpress_site(db, rec.wordpress_site_id, user_id)
    if not wp_site:
        raise HTTPException(status_code=404, detail="WordPress site not found")

    if rec.wp_content_type == "page":
        result = await delete_page_from_wordpress(wp_site, rec.wp_content_id)
    else:
        result = await delete_post_from_wordpress(wp_site, rec.wp_content_id)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("message", "Unpublish failed"))

    site = get_authority_site(db, rec.authority_site_id, user_id)
    if rec.node_type == "money":
        site.money_page_wp_page_id = None
        site.money_page_wp_link = None
        site.money_page_status = AuthorityNodeStatus.DRAFT
    elif rec.node_type == "service":
        cluster = next((c for c in site.service_clusters if c.id == rec.node_id), None)
        if cluster:
            cluster.wp_page_id = None
            cluster.wp_link = None
            cluster.status = AuthorityNodeStatus.DRAFT
    elif rec.node_type == "blog":
        for c in site.service_clusters:
            blog = next((b for b in c.blog_posts if b.id == rec.node_id), None)
            if blog:
                blog.wp_post_id = None
                blog.wp_link = None
                blog.status = AuthorityNodeStatus.DRAFT
                break

    db.delete(rec)
    db.commit()
    return get_authority_site(db, rec.authority_site_id, user_id)
