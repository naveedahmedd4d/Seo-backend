"""Authority Builder service — site architecture, clusters, blogs, link graph."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.models.authority_site import (
    AuthorityBlogPost,
    AuthorityLinkEdge,
    AuthorityLinkStatus,
    AuthorityLinkType,
    AuthorityNodeStatus,
    AuthorityPublishedNode,
    AuthorityServiceCluster,
    AuthoritySite,
)
from app.models.wordpress_site import WordPressSite
from app.services.wordpress_service import get_wordpress_site

MAX_CLUSTERS = 5
MAX_BLOGS_PER_CLUSTER = 5
DEFAULT_BLOGS_PER_CLUSTER = 4

CLUSTER_COLORS = ["blue", "green", "orange", "pink", "purple"]


def slugify(text: str) -> str:
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return s[:80] or "page"


def _default_blog_templates(service_title: str) -> List[Dict[str, str]]:
    st = service_title.strip()
    return [
        {
            "title": f"How much does {st.lower()} cost?",
            "focus_keyword": f"{st.lower()} cost",
            "article_type": "cost_guide",
        },
        {
            "title": f"{st} comparison guide",
            "focus_keyword": f"{st.lower()} comparison",
            "article_type": "comparison",
        },
        {
            "title": f"{st} timeline and process",
            "focus_keyword": f"{st.lower()} process",
            "article_type": "process",
        },
        {
            "title": f"How to choose {st.lower()}",
            "focus_keyword": f"how to choose {st.lower()}",
            "article_type": "buyer_guide",
        },
    ]


def get_authority_site(
    db: Session, site_id: int, user_id: int
) -> AuthoritySite:
    site = (
        db.query(AuthoritySite)
        .options(
            joinedload(AuthoritySite.service_clusters).joinedload(
                AuthorityServiceCluster.blog_posts
            ),
            joinedload(AuthoritySite.link_edges),
            joinedload(AuthoritySite.published_nodes),
            joinedload(AuthoritySite.wordpress_site),
        )
        .filter(AuthoritySite.id == site_id, AuthoritySite.user_id == user_id)
        .first()
    )
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Authority site not found")
    return site


def list_authority_sites(db: Session, user_id: int) -> List[AuthoritySite]:
    return (
        db.query(AuthoritySite)
        .options(joinedload(AuthoritySite.wordpress_site))
        .filter(AuthoritySite.user_id == user_id)
        .order_by(AuthoritySite.created_at.desc())
        .all()
    )


def create_authority_site(
    db: Session,
    user_id: int,
    wordpress_site_id: int,
    money_page_title: str,
    money_page_focus_keyword: str,
    money_page_slug: Optional[str] = None,
) -> AuthoritySite:
    wp = get_wordpress_site(db, wordpress_site_id, user_id)
    if not wp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WordPress site not found")

    existing = (
        db.query(AuthoritySite)
        .filter(
            AuthoritySite.user_id == user_id,
            AuthoritySite.wordpress_site_id == wordpress_site_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authority site already exists for this WordPress site",
        )

    title = money_page_title.strip()
    keyword = money_page_focus_keyword.strip()
    if not title or not keyword:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Money page title and focus keyword are required",
        )

    site = AuthoritySite(
        user_id=user_id,
        wordpress_site_id=wordpress_site_id,
        money_page_title=title,
        money_page_slug=money_page_slug.strip() if money_page_slug else slugify(keyword),
        money_page_focus_keyword=keyword,
        money_page_status=AuthorityNodeStatus.PLANNED,
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return get_authority_site(db, site.id, user_id)


def update_money_page(
    db: Session,
    site_id: int,
    user_id: int,
    *,
    title: Optional[str] = None,
    slug: Optional[str] = None,
    focus_keyword: Optional[str] = None,
    status: Optional[AuthorityNodeStatus] = None,
) -> AuthoritySite:
    site = get_authority_site(db, site_id, user_id)
    if title is not None:
        site.money_page_title = title.strip()
    if slug is not None:
        site.money_page_slug = slugify(slug)
    if focus_keyword is not None:
        site.money_page_focus_keyword = focus_keyword.strip()
    if status is not None:
        site.money_page_status = status
    db.commit()
    return get_authority_site(db, site_id, user_id)


def update_money_page_content(
    db: Session,
    site_id: int,
    user_id: int,
    *,
    body: str,
    meta_description: Optional[str] = None,
    seo_title: Optional[str] = None,
) -> AuthoritySite:
    site = get_authority_site(db, site_id, user_id)
    site.money_page_body = body
    if meta_description is not None:
        site.money_page_meta_description = meta_description
    if seo_title is not None:
        site.money_page_seo_title = seo_title
    if site.money_page_status == AuthorityNodeStatus.PLANNED and body.strip():
        site.money_page_status = AuthorityNodeStatus.DRAFT
    db.commit()
    return get_authority_site(db, site_id, user_id)


def update_cluster_content(
    db: Session,
    cluster_id: int,
    user_id: int,
    *,
    body: str,
    meta_description: Optional[str] = None,
    seo_title: Optional[str] = None,
) -> AuthoritySite:
    cluster = _get_cluster(db, cluster_id, user_id)
    cluster.body = body
    if meta_description is not None:
        cluster.meta_description = meta_description
    if seo_title is not None:
        cluster.seo_title = seo_title
    if cluster.status == AuthorityNodeStatus.PLANNED and body.strip():
        cluster.status = AuthorityNodeStatus.DRAFT
    db.commit()
    return get_authority_site(db, cluster.authority_site_id, user_id)


def update_blog_content(
    db: Session,
    blog_id: int,
    user_id: int,
    *,
    body: str,
    meta_description: Optional[str] = None,
    seo_title: Optional[str] = None,
) -> AuthoritySite:
    blog = _get_blog(db, blog_id, user_id)
    blog.body = body
    if meta_description is not None:
        blog.meta_description = meta_description
    if seo_title is not None:
        blog.seo_title = seo_title
    if blog.status == AuthorityNodeStatus.PLANNED and body.strip():
        blog.status = AuthorityNodeStatus.DRAFT
    db.commit()
    return get_authority_site(db, blog.cluster.authority_site_id, user_id)


def _get_cluster(db: Session, cluster_id: int, user_id: int) -> AuthorityServiceCluster:
    cluster = (
        db.query(AuthorityServiceCluster)
        .join(AuthoritySite)
        .options(joinedload(AuthorityServiceCluster.blog_posts))
        .filter(
            AuthorityServiceCluster.id == cluster_id,
            AuthoritySite.user_id == user_id,
        )
        .first()
    )
    if not cluster:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service cluster not found")
    return cluster


def _get_blog(db: Session, blog_id: int, user_id: int) -> AuthorityBlogPost:
    blog = (
        db.query(AuthorityBlogPost)
        .join(AuthorityServiceCluster)
        .join(AuthoritySite)
        .options(joinedload(AuthorityBlogPost.cluster))
        .filter(AuthorityBlogPost.id == blog_id, AuthoritySite.user_id == user_id)
        .first()
    )
    if not blog:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")
    return blog


def create_cluster(
    db: Session,
    site_id: int,
    user_id: int,
    title: str,
    focus_keyword: str,
    slug: Optional[str] = None,
    blog_titles: Optional[List[Dict[str, str]]] = None,
) -> AuthoritySite:
    site = get_authority_site(db, site_id, user_id)
    cluster_count = len(site.service_clusters)
    if cluster_count >= MAX_CLUSTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_CLUSTERS} service clusters allowed",
        )

    t = title.strip()
    kw = focus_keyword.strip()
    if not t or not kw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Service title and focus keyword are required",
        )

    cluster = AuthorityServiceCluster(
        authority_site_id=site.id,
        title=t,
        slug=slugify(slug or kw),
        focus_keyword=kw,
        status=AuthorityNodeStatus.PLANNED,
        color=CLUSTER_COLORS[cluster_count % len(CLUSTER_COLORS)],
        sort_order=cluster_count,
    )
    db.add(cluster)
    db.flush()

    templates = blog_titles if blog_titles else _default_blog_templates(t)
    for i, tmpl in enumerate(templates[:DEFAULT_BLOGS_PER_CLUSTER]):
        bt = (tmpl.get("title") or "").strip()
        bkw = (tmpl.get("focus_keyword") or bt).strip()
        db.add(
            AuthorityBlogPost(
                cluster_id=cluster.id,
                title=bt,
                slug=slugify(bkw),
                focus_keyword=bkw,
                article_type=tmpl.get("article_type"),
                status=AuthorityNodeStatus.PLANNED,
                sort_order=i,
            )
        )

    db.commit()
    rebuild_links(db, site_id, user_id)
    return get_authority_site(db, site_id, user_id)


def update_cluster(
    db: Session,
    cluster_id: int,
    user_id: int,
    *,
    title: Optional[str] = None,
    slug: Optional[str] = None,
    focus_keyword: Optional[str] = None,
    status: Optional[AuthorityNodeStatus] = None,
) -> AuthoritySite:
    cluster = _get_cluster(db, cluster_id, user_id)
    if title is not None:
        cluster.title = title.strip()
    if slug is not None:
        cluster.slug = slugify(slug)
    if focus_keyword is not None:
        cluster.focus_keyword = focus_keyword.strip()
    if status is not None:
        cluster.status = status
    db.commit()
    return get_authority_site(db, cluster.authority_site_id, user_id)


def delete_cluster(db: Session, cluster_id: int, user_id: int) -> AuthoritySite:
    cluster = _get_cluster(db, cluster_id, user_id)
    site_id = cluster.authority_site_id
    db.delete(cluster)
    db.commit()
    rebuild_links(db, site_id, user_id)
    return get_authority_site(db, site_id, user_id)


def update_blog(
    db: Session,
    blog_id: int,
    user_id: int,
    *,
    title: Optional[str] = None,
    slug: Optional[str] = None,
    focus_keyword: Optional[str] = None,
    status: Optional[AuthorityNodeStatus] = None,
    article_type: Optional[str] = None,
) -> AuthoritySite:
    blog = _get_blog(db, blog_id, user_id)
    if title is not None:
        blog.title = title.strip()
    if slug is not None:
        blog.slug = slugify(slug)
    if focus_keyword is not None:
        blog.focus_keyword = focus_keyword.strip()
    if status is not None:
        blog.status = status
    if article_type is not None:
        blog.article_type = article_type
    site_id = blog.cluster.authority_site_id
    db.commit()
    return get_authority_site(db, site_id, user_id)


def add_blog(
    db: Session,
    cluster_id: int,
    user_id: int,
    title: str,
    focus_keyword: str,
    slug: Optional[str] = None,
    article_type: Optional[str] = None,
) -> AuthoritySite:
    cluster = _get_cluster(db, cluster_id, user_id)
    if len(cluster.blog_posts) >= MAX_BLOGS_PER_CLUSTER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_BLOGS_PER_CLUSTER} blogs per cluster",
        )
    t = title.strip()
    kw = focus_keyword.strip()
    if not t or not kw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Blog title and focus keyword are required",
        )
    blog = AuthorityBlogPost(
        cluster_id=cluster.id,
        title=t,
        slug=slugify(slug or kw),
        focus_keyword=kw,
        article_type=article_type,
        status=AuthorityNodeStatus.PLANNED,
        sort_order=len(cluster.blog_posts),
    )
    db.add(blog)
    db.commit()
    rebuild_links(db, cluster.authority_site_id, user_id)
    return get_authority_site(db, cluster.authority_site_id, user_id)


def delete_blog(db: Session, blog_id: int, user_id: int) -> AuthoritySite:
    blog = _get_blog(db, blog_id, user_id)
    site_id = blog.cluster.authority_site_id
    db.delete(blog)
    db.commit()
    rebuild_links(db, site_id, user_id)
    return get_authority_site(db, site_id, user_id)


def _edge_exists(
    edges: List[AuthorityLinkEdge],
    source_type: str,
    source_id: int,
    target_type: str,
    target_id: int,
    link_type: AuthorityLinkType,
) -> bool:
    for e in edges:
        if (
            e.source_type == source_type
            and e.source_id == source_id
            and e.target_type == target_type
            and e.target_id == target_id
            and e.link_type == link_type
        ):
            return True
    return False


def rebuild_links(db: Session, site_id: int, user_id: int) -> AuthoritySite:
    site = get_authority_site(db, site_id, user_id)

    db.query(AuthorityLinkEdge).filter(
        AuthorityLinkEdge.authority_site_id == site.id
    ).delete()

    edges: List[AuthorityLinkEdge] = []

    for cluster in site.service_clusters:
        edges.append(
            AuthorityLinkEdge(
                authority_site_id=site.id,
                source_type="service",
                source_id=cluster.id,
                target_type="money",
                target_id=site.id,
                link_type=AuthorityLinkType.SERVICE_TO_MONEY,
                anchor_text=site.money_page_focus_keyword,
                status=AuthorityLinkStatus.PLANNED,
            )
        )

        blogs = sorted(cluster.blog_posts, key=lambda b: b.sort_order)
        for blog in blogs:
            edges.append(
                AuthorityLinkEdge(
                    authority_site_id=site.id,
                    source_type="blog",
                    source_id=blog.id,
                    target_type="service",
                    target_id=cluster.id,
                    link_type=AuthorityLinkType.BLOG_TO_SERVICE,
                    anchor_text=cluster.focus_keyword,
                    status=AuthorityLinkStatus.PLANNED,
                )
            )

        for i, blog in enumerate(blogs):
            if i + 1 < len(blogs):
                sibling = blogs[i + 1]
                edges.append(
                    AuthorityLinkEdge(
                        authority_site_id=site.id,
                        source_type="blog",
                        source_id=blog.id,
                        target_type="blog",
                        target_id=sibling.id,
                        link_type=AuthorityLinkType.BLOG_TO_BLOG,
                        anchor_text=sibling.title,
                        status=AuthorityLinkStatus.PLANNED,
                    )
                )
                edges.append(
                    AuthorityLinkEdge(
                        authority_site_id=site.id,
                        source_type="blog",
                        source_id=sibling.id,
                        target_type="blog",
                        target_id=blog.id,
                        link_type=AuthorityLinkType.BLOG_TO_BLOG,
                        anchor_text=blog.title,
                        status=AuthorityLinkStatus.PLANNED,
                    )
                )

    for edge in edges:
        db.add(edge)

    db.commit()
    return get_authority_site(db, site_id, user_id)


def _count_blog_links(blog_id: int, edges: List[AuthorityLinkEdge]) -> int:
    count = 0
    for e in edges:
        if e.source_type == "blog" and e.source_id == blog_id:
            count += 1
        if e.target_type == "blog" and e.target_id == blog_id:
            count += 1
    return count


def compute_stats_and_health(site: AuthoritySite) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    clusters = site.service_clusters
    all_blogs: List[AuthorityBlogPost] = []
    for c in clusters:
        all_blogs.extend(c.blog_posts)

    edges = site.link_edges or []

    service_live = sum(1 for c in clusters if c.status == AuthorityNodeStatus.PUBLISHED)
    service_draft = sum(1 for c in clusters if c.status == AuthorityNodeStatus.DRAFT)
    blog_published = sum(1 for b in all_blogs if b.status == AuthorityNodeStatus.PUBLISHED)
    blog_draft = sum(1 for b in all_blogs if b.status == AuthorityNodeStatus.DRAFT)

    stats = {
        "money_page_count": 1,
        "money_page_live": 1 if site.money_page_status == AuthorityNodeStatus.PUBLISHED else 0,
        "service_hubs_total": len(clusters),
        "service_hubs_live": service_live,
        "service_hubs_draft": service_draft,
        "blog_posts_total": len(all_blogs),
        "blog_posts_published": blog_published,
        "blog_posts_draft": blog_draft,
        "internal_links_total": len(edges),
        "internal_links_this_week": 0,
    }

    blogs_with_service = set()
    blogs_with_sibling = set()
    services_with_money = set()

    for e in edges:
        if e.link_type == AuthorityLinkType.BLOG_TO_SERVICE:
            blogs_with_service.add(e.source_id)
        elif e.link_type == AuthorityLinkType.BLOG_TO_BLOG:
            blogs_with_sibling.add(e.source_id)
            blogs_with_sibling.add(e.target_id)
        elif e.link_type == AuthorityLinkType.SERVICE_TO_MONEY:
            services_with_money.add(e.source_id)

    total_blogs = len(all_blogs)
    total_services = len(clusters)

    blog_to_service_pct = (
        round(100 * len(blogs_with_service) / total_blogs) if total_blogs else 100
    )
    service_to_money_pct = (
        round(100 * len(services_with_money) / total_services) if total_services else 100
    )
    sibling_links_pct = (
        round(100 * len(blogs_with_sibling) / total_blogs) if total_blogs else 100
    )

    issues: List[Dict[str, Any]] = []

    for cluster in clusters:
        cluster_blogs = cluster.blog_posts
        missing_sibling = [
            b for b in cluster_blogs if b.id not in blogs_with_sibling and len(cluster_blogs) > 1
        ]
        if missing_sibling:
            issues.append(
                {
                    "id": f"sibling-{cluster.id}",
                    "severity": "warning",
                    "message": (
                        f"{len(missing_sibling)} blog(s) missing sibling link in "
                        f"{cluster.title} cluster"
                    ),
                    "cluster_id": cluster.id,
                    "fixable": True,
                }
            )
        elif cluster_blogs:
            issues.append(
                {
                    "id": f"ok-{cluster.id}",
                    "severity": "success",
                    "message": f"{cluster.title} cluster fully linked — no issues",
                    "cluster_id": cluster.id,
                    "fixable": False,
                }
            )

        if cluster.id not in services_with_money:
            issues.append(
                {
                    "id": f"money-{cluster.id}",
                    "severity": "warning",
                    "message": f"{cluster.title} hub has no uplink to money page",
                    "cluster_id": cluster.id,
                    "fixable": True,
                }
            )

    link_health = {
        "blog_to_service_pct": blog_to_service_pct,
        "service_to_money_pct": service_to_money_pct,
        "sibling_links_pct": sibling_links_pct,
        "issues": issues,
    }

    return stats, link_health


def serialize_site(site: AuthoritySite) -> Dict[str, Any]:
    stats, link_health = compute_stats_and_health(site)
    edges = site.link_edges or []
    pub_map = {
        (p.node_type, p.node_id): p.id for p in (site.published_nodes or [])
    }

    wp = site.wordpress_site
    domain = ""
    name = ""
    if wp:
        domain = (wp.url or "").replace("https://", "").replace("http://", "").rstrip("/")
        name = wp.name or domain

    service_hubs = []
    for cluster in sorted(site.service_clusters, key=lambda c: c.sort_order):
        blogs = []
        for blog in sorted(cluster.blog_posts, key=lambda b: b.sort_order):
            blogs.append(
                {
                    "id": blog.id,
                    "title": blog.title,
                    "slug": blog.slug,
                    "focus_keyword": blog.focus_keyword,
                    "status": blog.status.value,
                    "article_type": blog.article_type,
                    "link_count": _count_blog_links(blog.id, edges),
                    "body": blog.body,
                    "meta_description": blog.meta_description,
                    "seo_title": blog.seo_title,
                    "wp_link": blog.wp_link,
                    "generation_error": blog.generation_error,
                    "is_generating": bool(blog.is_generating),
                    "published_id": pub_map.get(("blog", blog.id)),
                }
            )
        service_hubs.append(
            {
                "id": cluster.id,
                "title": cluster.title,
                "slug": cluster.slug,
                "focus_keyword": cluster.focus_keyword,
                "status": cluster.status.value,
                "color": cluster.color,
                "sort_order": cluster.sort_order,
                "blog_count": len(blogs),
                "blogs": blogs,
                "body": cluster.body,
                "meta_description": cluster.meta_description,
                "seo_title": cluster.seo_title,
                "wp_link": cluster.wp_link,
                "generation_error": cluster.generation_error,
                "is_generating": bool(cluster.is_generating),
                "published_id": pub_map.get(("service", cluster.id)),
            }
        )

    inbound_service_count = sum(
        1 for e in edges if e.link_type == AuthorityLinkType.SERVICE_TO_MONEY
    )

    return {
        "id": site.id,
        "name": name,
        "domain": domain,
        "wordpress_site_id": site.wordpress_site_id,
        "money_page": {
            "title": site.money_page_title,
            "slug": site.money_page_slug,
            "focus_keyword": site.money_page_focus_keyword,
            "status": site.money_page_status.value,
            "inbound_service_count": inbound_service_count,
            "body": site.money_page_body,
            "meta_description": site.money_page_meta_description,
            "seo_title": site.money_page_seo_title,
            "wp_link": site.money_page_wp_link,
            "generation_error": site.money_page_generation_error,
            "is_generating": bool(site.money_page_is_generating),
            "published_id": pub_map.get(("money", site.id)),
        },
        "service_hubs": service_hubs,
        "stats": stats,
        "link_health": link_health,
    }


def serialize_site_summary(site: AuthoritySite) -> Dict[str, Any]:
    stats, _ = compute_stats_and_health(site)
    wp = site.wordpress_site
    domain = ""
    name = ""
    if wp:
        domain = (wp.url or "").replace("https://", "").replace("http://", "").rstrip("/")
        name = wp.name or domain
    return {
        "id": site.id,
        "name": name,
        "domain": domain,
        "wordpress_site_id": site.wordpress_site_id,
        "money_page_title": site.money_page_title,
        "stats": stats,
    }
