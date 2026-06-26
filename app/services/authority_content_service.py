"""Authority Builder content generation — business prompts only."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.authority_site import (
    AuthorityBlogPost,
    AuthorityNodeStatus,
    AuthorityServiceCluster,
    AuthoritySite,
)
from app.services.authority_link_service import get_outbound_edges
from app.services.authority_service import get_authority_site
from app.services.openrouter_service import (
    generate_blog_seo_content,
    generate_faqs,
    generate_meta_description,
    generate_seo_title,
    _blog_delay,
)
from app.services.trivly_service import research_keywords

logger = logging.getLogger(__name__)

ARTICLE_TYPE_HINTS = {
    "cost_guide": "Write a pricing and cost breakdown guide. Include factors affecting price, typical ranges, and what to budget for.",
    "comparison": "Write a comparison guide (e.g. DIY vs professional, or option A vs B). Be balanced and practical.",
    "process": "Write a timeline and process explainer. Cover steps, duration, and what the client should expect.",
    "buyer_guide": "Write a how-to-choose buyer guide with checklist and decision criteria.",
}


async def _research_snippet(keyword: str) -> str:
    try:
        results = await research_keywords(keyword)
        if isinstance(results, dict):
            answer = results.get("answer") or ""
            if answer:
                return str(answer)[:2000]
            items = results.get("results") or []
            if items and isinstance(items, list):
                return "\n".join(
                    str(i.get("content", i.get("snippet", "")))[:300]
                    for i in items[:5]
                    if isinstance(i, dict)
                )[:2000]
    except Exception as e:
        logger.warning("Authority research failed for %s: %s", keyword, e)
    return ""


def _service_list_block(site: AuthoritySite) -> str:
    lines = []
    for c in sorted(site.service_clusters, key=lambda x: x.sort_order):
        lines.append(f"- {c.title} (slug: /{c.slug}, keyword: {c.focus_keyword})")
    return "\n".join(lines) if lines else "(no service pages yet)"


async def generate_money_page_content(
    site: AuthoritySite,
    *,
    include_research: bool = False,
) -> Dict[str, str]:
    brand = site.wordpress_site.name if site.wordpress_site else "Our company"
    research = await _research_snippet(site.money_page_focus_keyword) if include_research else ""
    research_block = f"\nResearch context:\n{research}\n" if research else ""

    prompt = f"""You are an expert SEO copywriter for a commercial service business.

Write a MONEY PAGE (primary commercial landing page) in Markdown.

Brand: {brand}
Focus keyword: {site.money_page_focus_keyword}
Page title: {site.money_page_title}
URL slug: /{site.money_page_slug}

Service pages to link to in a "Our services" section (use markdown links with these slugs):
{_service_list_block(site)}
{research_block}

Requirements:
- H1 must include the focus keyword naturally
- 1,500-2,000 words
- Sections: value proposition, who we serve, our services (grid with links to each service slug), why choose us, process overview, trust signals, location/service area placeholder, FAQs (4-6), strong CTA
- Commercial but helpful tone — not celebrity/news style
- Use markdown [text](/slug) for internal service links
- Do NOT invent fake client names or unverifiable awards

Return ONLY the markdown body, no JSON wrapper."""

    body = await generate_blog_seo_content(prompt, max_tokens=4000, temperature=0.55)
    seo_title = await generate_seo_title(
        site.money_page_title, keywords=[site.money_page_focus_keyword], category="Lifestyle & Misc"
    )
    meta = await generate_meta_description(
        site.money_page_title, keywords=[site.money_page_focus_keyword], category="Lifestyle & Misc"
    )
    return {"body": body.strip(), "seo_title": seo_title, "meta_description": meta}


async def generate_service_page_content(
    site: AuthoritySite,
    cluster: AuthorityServiceCluster,
    *,
    include_research: bool = False,
) -> Dict[str, str]:
    brand = site.wordpress_site.name if site.wordpress_site else "Our company"
    research = await _research_snippet(cluster.focus_keyword) if include_research else ""
    research_block = f"\nResearch context:\n{research}\n" if research else ""
    blog_titles = "\n".join(f"- {b.title} (/{b.slug})" for b in cluster.blog_posts)

    prompt = f"""You are an expert SEO copywriter for a commercial service business.

Write a SERVICE PAGE in Markdown.

Brand: {brand}
Service: {cluster.title}
Focus keyword: {cluster.focus_keyword}
URL slug: /{cluster.slug}
Money page keyword: {site.money_page_focus_keyword} (link to /{site.money_page_slug} in CTA)

Related blog guides in this cluster:
{blog_titles or "(none yet)"}
{research_block}

Requirements:
- H1 with focus keyword
- 1,200-1,800 words
- Sections: what we offer, deliverables, who it's for, process, pricing signals (ranges OK), mini case study placeholder, FAQs (4-5), CTA linking to money page
- Include a "Related guides" section listing the blog titles
- Commercial service tone — not biography/celebrity content

Return ONLY markdown body."""

    body = await generate_blog_seo_content(prompt, max_tokens=3500, temperature=0.55)
    seo_title = await generate_seo_title(
        cluster.title, keywords=[cluster.focus_keyword], category="Lifestyle & Misc"
    )
    meta = await generate_meta_description(
        cluster.title, keywords=[cluster.focus_keyword], category="Lifestyle & Misc"
    )
    return {"body": body.strip(), "seo_title": seo_title, "meta_description": meta}


async def generate_blog_content(
    site: AuthoritySite,
    cluster: AuthorityServiceCluster,
    blog: AuthorityBlogPost,
    *,
    include_research: bool = False,
) -> Dict[str, str]:
    brand = site.wordpress_site.name if site.wordpress_site else "Our company"
    research = await _research_snippet(blog.focus_keyword) if include_research else ""
    research_block = f"\nResearch context:\n{research}\n" if research else ""
    type_hint = ARTICLE_TYPE_HINTS.get(blog.article_type or "", "Write an informative guide.")

    siblings = [b.title for b in cluster.blog_posts if b.id != blog.id]
    edges = site.link_edges or []
    outbound = get_outbound_edges(edges, "blog", blog.id)
    link_notes = "\n".join(
        f"- Link to {e.target_type} id {e.target_id} as [{e.anchor_text}](/slug)"
        for e in outbound
    )

    prompt = f"""You are an expert SEO content writer for a service business blog.

Write an informational BLOG ARTICLE in Markdown.

Brand: {brand}
Article title: {blog.title}
Focus keyword: {blog.focus_keyword}
URL slug: /{blog.slug}
Article type: {blog.article_type or "guide"}
Parent service: {cluster.title} (/{cluster.slug}) — include 1-2 natural links to this service page
Money page: /{site.money_page_slug} (only link if comparing providers/locations — do not force)

Sibling articles to reference: {", ".join(siblings) if siblings else "none"}

Internal linking plan:
{link_notes or "- Link to parent service page"}

{type_hint}
{research_block}

Requirements:
- 1,000-1,500 words
- H1 with focus keyword
- Helpful, expert tone — soft CTA to parent service at end
- Use markdown [text](/slug) for internal links
- NOT celebrity/biography content

Return ONLY markdown body."""

    body = await generate_blog_seo_content(prompt, max_tokens=3000, temperature=0.55)
    seo_title = await generate_seo_title(
        blog.title, keywords=[blog.focus_keyword], category="Lifestyle & Misc"
    )
    meta = await generate_meta_description(
        blog.title, keywords=[blog.focus_keyword], category="Lifestyle & Misc"
    )
    return {"body": body.strip(), "seo_title": seo_title, "meta_description": meta}


async def run_generate_money_page(
    db: Session, site_id: int, user_id: int, include_research: bool = False
) -> None:
    site = get_authority_site(db, site_id, user_id)
    site.money_page_is_generating = True
    site.money_page_generation_error = None
    db.commit()
    try:
        result = await generate_money_page_content(site, include_research=include_research)
        site.money_page_body = result["body"]
        site.money_page_seo_title = result.get("seo_title")
        site.money_page_meta_description = result.get("meta_description")
        site.money_page_status = AuthorityNodeStatus.DRAFT
        site.money_page_is_generating = False
        site.money_page_generation_error = None
        db.commit()
    except Exception as e:
        logger.exception("Money page generation failed")
        site.money_page_is_generating = False
        site.money_page_generation_error = str(e)[:500]
        db.commit()


async def run_generate_service_page(
    db: Session, cluster_id: int, user_id: int, include_research: bool = False
) -> None:
    from app.services.authority_service import _get_cluster

    cluster = _get_cluster(db, cluster_id, user_id)
    site = get_authority_site(db, cluster.authority_site_id, user_id)
    cluster.is_generating = True
    cluster.generation_error = None
    db.commit()
    try:
        result = await generate_service_page_content(site, cluster, include_research=include_research)
        cluster.body = result["body"]
        cluster.seo_title = result.get("seo_title")
        cluster.meta_description = result.get("meta_description")
        cluster.status = AuthorityNodeStatus.DRAFT
        cluster.is_generating = False
        cluster.generation_error = None
        db.commit()
    except Exception as e:
        logger.exception("Service page generation failed")
        cluster.is_generating = False
        cluster.generation_error = str(e)[:500]
        db.commit()


async def run_generate_blog(
    db: Session, blog_id: int, user_id: int, include_research: bool = False
) -> None:
    from app.services.authority_service import _get_blog

    blog = _get_blog(db, blog_id, user_id)
    site = get_authority_site(db, blog.cluster.authority_site_id, user_id)
    cluster = blog.cluster
    blog.is_generating = True
    blog.generation_error = None
    db.commit()
    try:
        result = await generate_blog_content(site, cluster, blog, include_research=include_research)
        blog.body = result["body"]
        blog.seo_title = result.get("seo_title")
        blog.meta_description = result.get("meta_description")
        blog.status = AuthorityNodeStatus.DRAFT
        blog.is_generating = False
        blog.generation_error = None
        db.commit()
    except Exception as e:
        logger.exception("Blog generation failed")
        blog.is_generating = False
        blog.generation_error = str(e)[:500]
        db.commit()


async def run_generate_cluster_blogs(
    db: Session, cluster_id: int, user_id: int, include_research: bool = False
) -> None:
    from app.services.authority_service import _get_cluster

    cluster = _get_cluster(db, cluster_id, user_id)
    for blog in sorted(cluster.blog_posts, key=lambda b: b.sort_order):
        await run_generate_blog(db, blog.id, user_id, include_research=include_research)
        await _blog_delay()


async def bg_run_generate_money_page(
    site_id: int, user_id: int, include_research: bool = False
) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await run_generate_money_page(db, site_id, user_id, include_research)
    finally:
        db.close()


async def bg_run_generate_service_page(
    cluster_id: int, user_id: int, include_research: bool = False
) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await run_generate_service_page(db, cluster_id, user_id, include_research)
    finally:
        db.close()


async def bg_run_generate_blog(
    blog_id: int, user_id: int, include_research: bool = False
) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await run_generate_blog(db, blog_id, user_id, include_research)
    finally:
        db.close()


async def bg_run_generate_cluster_blogs(
    cluster_id: int, user_id: int, include_research: bool = False
) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        await run_generate_cluster_blogs(db, cluster_id, user_id, include_research)
    finally:
        db.close()
