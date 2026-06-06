import copy
import re
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Body
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import Optional, Dict, Any, List
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.workflow_state import WorkflowType, WorkflowStatus
from app.services.langgraph_service import get_workflow_state
from app.services.openrouter_service import (
    assess_research_sufficiency,
    assess_religion_verifiability,
    resolve_celebrity_display_name,
    generate_seo_title,
    generate_meta_description,
    generate_biography_news_meta_description,
    generate_blog_body,
    generate_faqs,
    generate_related_queries,
    generate_internal_links,
    generate_schema_markup,
    extract_keyword_clusters,
    _blog_delay,
    generate_single_blog_batch,
    generate_tree_titles_metas_batch,
    generate_tree_faqs_queries_batch,
    select_featured_image,
    sanitize_religion_body,
)
from app.services.serpapi_service import search_images as serpapi_search_images
from app.services.trivly_service import research_keywords, find_youtube_interview, search_youtube_interviews
from app.services.wordpress_service import publish_to_wordpress, get_wordpress_site, delete_post_from_wordpress
from app.services.blog_consistency import (
    validate_facts_across_articles,
    apply_canonical_net_worth,
    apply_canonical_zodiac,
    sanitize_filmography_table,
    sanitize_net_worth_body,
    sanitize_blog_body,
)
from app.schemas.blog import (
    BlogGenerateRequest,
    BlogGenerateResponse,
    BlogWorkflowStatus,
    BlogUpdateRequest,
    BlogWorkflowListItem,
    BlogWorkflowListResponse,
    BlogPublishRequest,
    BlogPublishResponse,
    BlogUnpublishRequest,
    BlogUnpublishResponse,
    PublishedPostItem,
    WorkflowPublishStatus,
)
from app.models.workflow_state import WorkflowState
from app.models.published_post import PublishedPost
from app.models.image_search_history import ImageSearchHistory
from app.config import settings
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

from app.services.research_context import (
    RESEARCH_CONTEXT_MAX_LENGTH,
    format_research_context,
)


def flat_tree_child_slug(parent_slug: str, intent: str) -> str:
    """WP post slug for tree children: {parent}-religion | {parent}-net-worth (flat, no /person/.../segment)."""
    ps = (parent_slug or "").strip()
    if intent == "religion":
        suffix = "religion"
    elif intent == "net_worth":
        suffix = "net-worth"
    else:
        suffix = re.sub(r"[^a-z0-9]+", "-", (intent or "").lower()).strip("-") or "article"
    if not ps:
        return suffix[:80]
    return f"{ps}-{suffix}"[:80]


def coalesce_tree_child_slug(
    parent_slug: str,
    stored: Optional[str],
    blog_key: str,
    child_intent: Optional[str] = None,
) -> str:
    """Resolve child slug to flat format; legacy values religion / net-worth expand with parent_slug.
    When religion was skipped, child_0 is net worth — pass child_intent='net_worth'."""
    st = (stored or "").strip()
    if child_intent in ("religion", "net_worth"):
        intent = child_intent
    else:
        intent = "religion" if blog_key == "child_0" else "net_worth"
    if st in ("religion", "net-worth"):
        return flat_tree_child_slug(parent_slug, "religion" if st == "religion" else "net_worth")
    if not st:
        return flat_tree_child_slug(parent_slug, intent)
    return st[:80]


def generate_slug(
    title: str,
    parent_slug: Optional[str] = None,
    intent: Optional[str] = None,
    focus_keyword: Optional[str] = None,
) -> str:
    """Generate URL slug. Rank Math: use focus keyword in URL when provided. Parent: slugify title or focus_keyword; children: {parent}-religion / {parent}-net-worth."""
    if parent_slug and intent:
        if intent in ("religion", "net_worth"):
            return flat_tree_child_slug(parent_slug, intent)
        return re.sub(r"[^a-z0-9]+", "-", intent.lower()).strip("-") or "article"
    # Parent: prefer focus_keyword for slug so URL contains focus keyword (Rank Math requirement)
    if focus_keyword and focus_keyword.strip():
        kw_slug = re.sub(r"[^a-z0-9\s-]", "", focus_keyword.lower().strip())
        kw_slug = re.sub(r"[-\s]+", "-", kw_slug).strip("-")
        if kw_slug:
            return kw_slug[:80]
    # Fallback: slugify the title or first part (e.g. "Who Is Elon Musk?" -> "elon-musk")
    text = title.split("?")[0].split(":")[0].strip() if title else ""
    if not text:
        return "article"
    slug = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug[:80] if slug else "article"


def _normalize_user_slug(raw: str, title: str) -> str:
    """Normalize user-edited slug for storage; empty input falls back to auto slug from title."""
    s = (raw or "").strip()
    if not s:
        return generate_slug(title or "")
    slug = re.sub(r"[^a-z0-9\s-]", "", s.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug[:80] if slug else generate_slug(title or "")


def rewrite_legacy_person_tree_paths(body: str) -> str:
    """Rewrite /person/{slug}/religion|net-worth (often hallucinated in tables) to flat /{slug}-religion|/{slug}-net-worth."""
    if not body or "/person/" not in body:
        return body
    s = re.sub(r"/person/([^/\s]+)/religion", r"/\1-religion", body)
    s = re.sub(r"/person/([^/\s]+)/net-worth", r"/\1-net-worth", s)
    return s


def _state_data_with_rewritten_tree_links(state_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a copy of state_data with tree article bodies rewritten for API responses (DB unchanged until PATCH)."""
    if not state_data or not state_data.get("is_tree"):
        return state_data
    sd = copy.deepcopy(state_data)
    parent = sd.get("parent")
    if isinstance(parent, dict) and parent.get("body_content"):
        parent["body_content"] = rewrite_legacy_person_tree_paths(str(parent["body_content"]))
    for ch in sd.get("children") or []:
        if isinstance(ch, dict) and ch.get("body_content"):
            ch["body_content"] = rewrite_legacy_person_tree_paths(str(ch["body_content"]))
    return sd


def _state_data_without_toc(state_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a copy of state_data with Table of Contents blocks removed from body fields."""
    if not state_data:
        return state_data
    sd = copy.deepcopy(state_data)
    if sd.get("body_content"):
        sd["body_content"] = sanitize_blog_body(str(sd["body_content"]))
    parent = sd.get("parent")
    if isinstance(parent, dict) and parent.get("body_content"):
        parent["body_content"] = sanitize_blog_body(str(parent["body_content"]))
    for ch in sd.get("children") or []:
        if isinstance(ch, dict) and ch.get("body_content"):
            ch["body_content"] = sanitize_blog_body(str(ch["body_content"]))
    return sd


def _state_data_for_api(state_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return _state_data_without_toc(_state_data_with_rewritten_tree_links(state_data))


def _inject_youtube_embed(body: str, youtube_url: Optional[str]) -> str:
    """Replace [YOUTUBE_EMBED: ...] placeholder with a proper YouTube iframe embed. Remove placeholder if no URL."""
    if not body:
        return body
    # Extract video ID (youtube.com/watch?v=ID or youtu.be/ID)
    video_id = None
    if youtube_url:
        if "youtu.be/" in youtube_url:
            parts = youtube_url.split("youtu.be/")
            if len(parts) >= 2:
                video_id = parts[1].split("?")[0].split("&")[0].strip("/")
        elif "watch?v=" in youtube_url:
            parts = youtube_url.split("watch?v=")
            if len(parts) >= 2:
                video_id = parts[1].split("&")[0].split("#")[0].strip()
    # Replace or remove placeholder
    if video_id:
        embed_html = (
            f'<iframe width="560" height="315" src="https://www.youtube.com/embed/{video_id}" '
            'title="YouTube video player" frameborder="0" '
            'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" '
            'allowfullscreen></iframe>'
        )
        body = re.sub(r"\[YOUTUBE_EMBED:[^\]]*\]", embed_html, body, count=1)
    else:
        body = re.sub(r"\[YOUTUBE_EMBED:[^\]]*\]", "", body, count=1)
    # Remove stray iframe attribute lines often output by the LLM as plain text
    body = re.sub(
        r'\n\s*title="YouTube video player"\s*\n',
        "\n", body,
    )
    body = re.sub(
        r'\n\s*frameborder="0"\s*\n',
        "\n", body,
    )
    body = re.sub(
        r'\n\s*allow="accelerometer;[^"]*"\s*\n',
        "\n", body,
    )
    body = re.sub(
        r'\n\s*allowfullscreen\s*>\s*\n',
        "\n", body,
    )
    return body


def _sanitize_conclusion_and_sources(body: str) -> str:
    """Fix malformed Conclusion/Sources: remove (blank) links, strip image placeholders from Sources line."""
    if not body:
        return body
    # Replace markdown links with placeholder URL "blank" — keep text only (no link)
    body = re.sub(r"\[([^\]]*)\]\((blank)\)", r"\1", body, flags=re.IGNORECASE)
    # Remove image placeholder that appears on the same line as Sources (e.g. "Sources: ... ![alt](image-url)")
    body = re.sub(
        r"(\|\s*\*\*Sources:\*\*[^|\n]*?)\s*!\[[^\]]*\]\s*\(\s*image-url\s*\)\s*",
        r"\1 ",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"(Sources:\s*[^\n]*?)\s*!\[[^\]]*\]\s*\(\s*image-url\s*\)\s*",
        r"\1 ",
        body,
        flags=re.IGNORECASE,
    )
    return body


def _normalize_related_queries(related_queries: List[Any]) -> List[Dict[str, str]]:
    """Normalize to list of {query, answer}; accept legacy list of strings."""
    out: List[Dict[str, str]] = []
    for x in (related_queries or [])[:12]:
        if isinstance(x, dict):
            out.append({"query": str(x.get("query", "")).strip(), "answer": str(x.get("answer", "")).strip()})
        else:
            out.append({"query": str(x).strip(), "answer": ""})
    return out


def _render_faqs_and_queries_html(faqs: List[Dict[str, Any]], related_queries: List[Any]) -> str:
    """Render FAQs and Related Search Queries as HTML for WordPress post content."""
    import html as html_module
    out: List[str] = []
    if faqs:
        out.append('<h2 id="faqs">Frequently Asked Questions</h2>')
        for faq in faqs[:10]:
            if not isinstance(faq, dict):
                continue
            q = str(faq.get("question", "")).strip()
            a = str(faq.get("answer", "")).strip()
            if q:
                out.append(f'<p><strong>{html_module.escape(q)}</strong></p>')
                if a:
                    out.append(f'<p>{html_module.escape(a)}</p>')
    rq = _normalize_related_queries(related_queries)
    if rq:
        out.append('<h2 id="related-queries">Related Search Queries</h2>')
        for item in rq:
            qs = item.get("query", "").strip()
            if not qs:
                continue
            out.append(f'<p><strong>{html_module.escape(qs)}</strong></p>')
            ans = item.get("answer", "").strip()
            if ans:
                out.append(f'<p>{html_module.escape(ans)}</p>')
    return "\n".join(out) if out else ""


def _render_faqs_and_queries_markdown(faqs: List[Dict[str, Any]], related_queries: List[Any]) -> str:
    """Render FAQs and Related Search Queries as markdown for WordPress post content."""
    out: List[str] = []
    if faqs:
        out.append("\n\n## Frequently Asked Questions\n")
        for faq in faqs[:10]:
            if not isinstance(faq, dict):
                continue
            q = str(faq.get("question", "")).strip()
            a = str(faq.get("answer", "")).strip()
            if q:
                out.append(f"\n**{q}**\n\n{a}\n" if a else f"\n**{q}**\n")
    rq = _normalize_related_queries(related_queries)
    if rq:
        out.append("\n\n## Related Search Queries\n\n")
        for item in rq:
            qs = item.get("query", "").strip()
            if not qs:
                continue
            ans = item.get("answer", "").strip()
            out.append(f"\n**{qs}**\n\n{ans}\n" if ans else f"\n- {qs}\n")
    return "\n".join(out) if out else ""


def inject_internal_links_into_body(
    body: str,
    internal_links: List[Dict[str, Any]],
    slug_map: Dict[str, str],
    religion_skipped: bool = False,
) -> str:
    """Inject internal links inline into body (first relevant phrase per link). Uses markdown [text](href); frontend/WordPress render as HTML anchor."""
    if not body or not internal_links:
        return body
    # Insertion hints: target -> list of phrases to find (first match wins)
    if religion_skipped:
        # Avoid matching "assets" — causes awkward "assets (their net worth)"
        target_phrases = {
            "child_0": ["net worth", "estimated net worth", "fortune", "wealth", "estimated at"],
            "parent": ["career", "overview", "who is", "biography"],
        }
        target_display_text = {
            "child_0": "their net worth",
            "parent": None,
        }
    else:
        target_phrases = {
            "child_1": ["net worth", "estimated net worth", "fortune", "wealth", "estimated at"],
            "child_0": ["religion", "religious beliefs", "faith", "spirituality", "beliefs"],
            "parent": ["career", "overview", "who is", "biography"],
        }
        target_display_text = {
            "child_0": "their religious beliefs",
            "child_1": "their net worth",
            "parent": None,
        }
    result = body
    used = set()
    for link in internal_links:
        target = link.get("target")
        if not target or target in used:
            continue
        href = slug_map.get(target)
        if not href:
            continue
        anchor = (link.get("anchor_text") or "read more").strip()
        if not anchor:
            continue
        display = target_display_text.get(target) or anchor
        phrases = target_phrases.get(target, [])
        for phrase in phrases:
            idx = result.lower().find(phrase)
            if idx == -1:
                continue
            end = idx + len(phrase)
            if result[end:end + 2] == "](":
                continue
            # Format: " (link)" so table shows "Religion (her/him religious beliefs)" with link
            markdown_link = f" ([{display}]({href}))"
            result = result[:end] + markdown_link + result[end:]
            used.add(target)
            break
    return result


CELEBRITY_CONTENT_TYPE_INTENTS: Dict[str, str] = {
    "biography": "parent_overview",
    "religious": "religion",
    "net_worth": "net_worth",
}


async def _run_celebrity_single_article(
    db: Session,
    workflow_state: WorkflowState,
    topic: str,
    description: Optional[str],
    keywords: list,
    category: str,
    research_context: Optional[str],
    celebrity_content_type: str,
    suggested_title: Optional[str] = None,
    suggested_meta_description: Optional[str] = None,
    news_context: Optional[str] = None,
) -> None:
    """Generate one celebrity article (biography, religious, or net worth) instead of the full tree."""
    content_intent = CELEBRITY_CONTENT_TYPE_INTENTS[celebrity_content_type]

    keyword_strategy: Optional[Dict[str, Any]] = None
    if research_context:
        try:
            keyword_strategy = await extract_keyword_clusters(research_context, topic)
        except Exception as e:
            logger.warning("Keyword clusters extraction failed: %s", e)
    await _blog_delay()

    youtube_url: Optional[str] = None
    if celebrity_content_type == "biography":
        try:
            youtube_url = await find_youtube_interview(topic)
        except Exception as e:
            logger.warning("YouTube interview lookup failed: %s", e)

    title = (suggested_title or "").strip()
    if not title:
        title = await generate_seo_title(
            topic, description, keywords, category,
            research_context=research_context, content_intent=content_intent,
        )
    await _blog_delay()
    meta_description = (suggested_meta_description or "").strip()
    if celebrity_content_type == "biography" and (suggested_title or news_context):
        from app.services.news_research_service import is_weak_biography_meta

        if not meta_description or is_weak_biography_meta(
            meta_description, suggested_title or title, None
        ):
            meta_description = await generate_biography_news_meta_description(
                topic,
                suggested_title or title,
                research_context=research_context,
                news_context=news_context,
            )
    elif not meta_description:
        meta_description = await generate_meta_description(
            topic, description, keywords, category,
            research_context=research_context, content_intent=content_intent,
        )
    await _blog_delay()

    body_kwargs: Dict[str, Any] = {
        "research_context": research_context,
        "content_intent": content_intent,
        "keyword_strategy": keyword_strategy,
    }
    if celebrity_content_type == "biography":
        body_kwargs["youtube_url"] = youtube_url
        body_kwargs["omit_religion_in_parent"] = True
    elif celebrity_content_type == "net_worth":
        body_kwargs["omit_religion_refs_in_net_worth"] = True

    body_content = await generate_blog_body(topic, description, keywords, category, **body_kwargs)
    await _blog_delay()

    if celebrity_content_type == "biography":
        body_content = _inject_youtube_embed(body_content, youtube_url)
        body_content = sanitize_filmography_table(body_content, topic)
    elif celebrity_content_type == "net_worth":
        body_content = sanitize_net_worth_body(body_content)

    faqs = await generate_faqs(
        topic, description, keywords, category,
        research_context=research_context, content_intent=content_intent,
    )
    await _blog_delay()
    related_queries_raw = await generate_related_queries(
        topic, description, keywords, category,
        research_context=research_context, content_intent=content_intent,
    )
    related_queries = _normalize_related_queries(related_queries_raw)

    body_content = sanitize_blog_body(body_content or "")
    if content_intent == "religion":
        body_content = sanitize_religion_body(body_content)
    body_content = _sanitize_conclusion_and_sources(body_content)
    body_content = rewrite_legacy_person_tree_paths(body_content)

    try:
        focus_keyword = await resolve_celebrity_display_name(topic, research_context)
    except Exception:
        focus_keyword = topic.strip() or (keywords[0] if keywords else None)
    if not focus_keyword:
        focus_keyword = topic.strip() or (keywords[0] if keywords else "article")

    if celebrity_content_type == "biography":
        slug = generate_slug(title, focus_keyword=focus_keyword)
    else:
        parent_slug = generate_slug("", focus_keyword=focus_keyword)
        slug = generate_slug("", parent_slug=parent_slug, intent=content_intent)

    state_data: Dict[str, Any] = {
        "topic": topic,
        "description": description,
        "keywords": keywords,
        "category": category,
        "celebrity_content_type": celebrity_content_type,
        "content_intent": content_intent,
        "title": title,
        "meta_description": meta_description,
        "body_content": body_content,
        "faqs": faqs,
        "related_queries": related_queries,
        "focus_keyword": focus_keyword,
        "slug": slug,
    }
    if keyword_strategy:
        state_data["keyword_strategy"] = keyword_strategy
    if research_context:
        state_data["research_summary"] = research_context[:500] + ("..." if len(research_context) > 500 else "")

    workflow_state.state_data = state_data
    workflow_state.status = WorkflowStatus.COMPLETED
    db.commit()


async def run_blog_workflow(
    db: Session,
    workflow_id: int,
    topic: str,
    description: Optional[str],
    keywords: list,
    category: str,
    user_id: int,
    include_research: bool = True,
    celebrity_content_type: Optional[str] = None,
    suggested_title: Optional[str] = None,
    suggested_meta_description: Optional[str] = None,
    news_context: Optional[str] = None,
):
    """Run the blog generation workflow with SEO-optimized content"""
    try:
        # Step 0: Research via Tavily (if enabled and configured)
        research_context: Optional[str] = None
        if include_research and settings.tavily_api_key:
            try:
                query = topic
                if description and description.strip():
                    desc_snippet = description.strip()[:200]
                    query = f"{topic}. {desc_snippet}"
                data = await research_keywords(db, query, user_id)
                research_context = format_research_context(data)
            except Exception as e:
                logger.warning("Research step failed, generating without research: %s", e)
                research_context = None

        # Step 0b: Research enrichment - if LLM says context insufficient, run extra Tavily queries (one round only)
        if include_research and research_context and settings.tavily_api_key:
            try:
                sufficient, extra_queries = await assess_research_sufficiency(topic, research_context, category)
                if not sufficient and extra_queries:
                    for eq in extra_queries:
                        try:
                            extra_data = await research_keywords(db, eq, user_id)
                            extra_formatted = format_research_context(extra_data)
                            if extra_formatted and len(research_context) + len(extra_formatted) + 2 < RESEARCH_CONTEXT_MAX_LENGTH * 2:
                                research_context = research_context + "\n\n" + extra_formatted
                        except Exception as e:
                            logger.warning("Enrichment query failed: %s", e)
            except Exception as e:
                logger.warning("Research sufficiency check failed, continuing with current context: %s", e)

        # Step 0c: Celebrity-targeted enrichment (Tavily) for better titles, filmography, zodiac
        if (
            include_research
            and research_context
            and settings.tavily_api_key
            and category == "Celebrities & Public Figures"
        ):
            from datetime import date as _date
            _year = _date.today().year
            _celebrity_queries = [
                f"{topic} net worth {_year}",
                f"{topic} date of birth zodiac",
                f"{topic} documentaries TV appearances",
            ]
            for _eq in _celebrity_queries:
                try:
                    _extra_data = await research_keywords(db, _eq, user_id)
                    _extra_fmt = format_research_context(_extra_data)
                    if _extra_fmt and len(research_context) + len(_extra_fmt) + 2 < RESEARCH_CONTEXT_MAX_LENGTH * 2:
                        research_context = research_context + "\n\n" + _extra_fmt
                except Exception as e:
                    logger.warning("Celebrity enrichment query failed: %s", e)

        if news_context and news_context.strip():
            news_block = news_context.strip()
            if research_context:
                research_context = news_block + "\n\n" + research_context
            else:
                research_context = news_block

        workflow_state = get_workflow_state(db, workflow_id, user_id)
        if not workflow_state:
            return

        if category != "Celebrities & Public Figures":
            # Single-blog flow (batched: 2 LLM calls instead of 6, shared context)
            batch = await generate_single_blog_batch(topic, description, keywords, category, research_context)
            title = batch["title"]
            meta_description = batch["meta_description"]
            body_content = sanitize_blog_body(batch["body_content"] or "")
            faqs = batch["faqs"]
            related_queries = batch["related_queries"]
            # Rank Math: focus keyword and slug for URL
            focus_keyword = (topic.strip() or (keywords[0] if keywords else "")) or "article"
            slug = generate_slug(title, focus_keyword=focus_keyword)
            state_data = {
                "topic": topic,
                "description": description,
                "keywords": keywords,
                "category": category,
                "title": title,
                "meta_description": meta_description,
                "body_content": body_content,
                "faqs": faqs,
                "related_queries": related_queries,
                "focus_keyword": focus_keyword,
                "slug": slug,
            }
            if research_context:
                state_data["research_summary"] = research_context[:500] + ("..." if len(research_context) > 500 else "")
            workflow_state.state_data = state_data
            workflow_state.status = WorkflowStatus.COMPLETED
            db.commit()
            return

        if celebrity_content_type and celebrity_content_type in CELEBRITY_CONTENT_TYPE_INTENTS:
            await _run_celebrity_single_article(
                db, workflow_state, topic, description, keywords, category,
                research_context, celebrity_content_type,
                suggested_title=suggested_title,
                suggested_meta_description=suggested_meta_description,
                news_context=news_context,
            )
            return

        # Tree flow: Celebrities & Public Figures (batched to reduce LLM calls and rate limits)
        keyword_strategy: Optional[Dict[str, Any]] = None
        if research_context:
            try:
                keyword_strategy = await extract_keyword_clusters(research_context, topic)
            except Exception as e:
                logger.warning("Keyword clusters extraction failed: %s", e)
        await _blog_delay()

        include_religion = True
        try:
            include_religion = await assess_religion_verifiability(topic, research_context)
        except Exception as e:
            logger.warning("Religion verifiability check failed, skipping religion article: %s", e)
            include_religion = False

        # Batch 1: titles + meta descriptions (1 or 3 child metas depending on include_religion)
        metas = await generate_tree_titles_metas_batch(
            topic, description, keywords, category, research_context, include_religion=include_religion
        )
        parent_title = metas["parent"]["title"]
        parent_meta = metas["parent"]["meta_description"]
        c1_title = (metas["child_0"].get("title") or "").strip() if include_religion else ""
        c1_meta = (metas["child_0"].get("meta_description") or "").strip() if include_religion else ""
        c2_title = metas["child_1"]["title"]
        c2_meta = metas["child_1"]["meta_description"]
        await _blog_delay()

        # Optional: find one YouTube interview for parent "First Media Appearance" section (Tavily)
        youtube_url = None
        try:
            youtube_url = await find_youtube_interview(topic)
        except Exception as e:
            logger.warning("YouTube interview lookup failed: %s", e)

        omit_parent_rel = not include_religion
        omit_nw_rel = not include_religion

        # Generate each body separately so parent and religion get full token budget (2,500-3,000 words each)
        parent_body = await generate_blog_body(
            topic, description, keywords, category,
            research_context=research_context,
            content_intent="parent_overview",
            keyword_strategy=keyword_strategy,
            youtube_url=youtube_url,
            omit_religion_in_parent=omit_parent_rel,
        )
        parent_body = _inject_youtube_embed(parent_body, youtube_url)
        await _blog_delay()
        c1_body = ""
        if include_religion:
            c1_body = await generate_blog_body(
                topic, description, keywords, category,
                research_context=research_context,
                content_intent="religion",
                keyword_strategy=keyword_strategy,
            )
            c1_body = sanitize_religion_body(c1_body)
            await _blog_delay()
        c2_body = await generate_blog_body(
            topic, description, keywords, category,
            research_context=research_context,
            content_intent="net_worth",
            keyword_strategy=keyword_strategy,
            omit_religion_refs_in_net_worth=omit_nw_rel,
        )
        c2_body = sanitize_net_worth_body(c2_body)
        await _blog_delay()

        parent_body = sanitize_blog_body(parent_body)
        c2_body = sanitize_blog_body(c2_body)
        # Remove filmography rows that are clearly not about the celebrity (e.g. wrong film title)
        parent_body = sanitize_filmography_table(parent_body, topic)

        # Batch 3: FAQs + related_queries
        faqs_queries = await generate_tree_faqs_queries_batch(
            topic, description, keywords, category, research_context, include_religion=include_religion
        )
        parent_faqs = faqs_queries["parent_faqs"]
        parent_queries = faqs_queries["parent_queries"]
        c1_faqs = faqs_queries["child_0_faqs"]
        c1_queries = faqs_queries["child_0_queries"]
        c2_faqs = faqs_queries["child_1_faqs"]
        c2_queries = faqs_queries["child_1_queries"]
        await _blog_delay()

        # Rank Math: use focus keyword in URL (slug); resolve full name for celebrities
        try:
            focus_keyword = await resolve_celebrity_display_name(topic, research_context)
        except Exception:
            focus_keyword = topic.strip() or (keywords[0] if keywords else None)
        if not focus_keyword:
            focus_keyword = topic.strip() or (keywords[0] if keywords else None)
        parent_slug = generate_slug(parent_title, focus_keyword=focus_keyword)
        c1_slug = generate_slug("", parent_slug=parent_slug, intent="religion") if include_religion else ""
        c2_slug = generate_slug("", parent_slug=parent_slug, intent="net_worth")

        # Consistency: cross-check net worth / children counts across body and FAQs; fix mismatches
        parent_node = {"body_content": parent_body, "faqs": parent_faqs}
        if include_religion:
            children_nodes = [
                {"body_content": c1_body, "faqs": c1_faqs},
                {"body_content": c2_body, "faqs": c2_faqs},
            ]
        else:
            children_nodes = [{"body_content": c2_body, "faqs": c2_faqs}]
        warnings_by_key, consistency_warnings = validate_facts_across_articles(parent_node, children_nodes)
        for w in consistency_warnings:
            logger.warning("Blog consistency: %s", w)
        canonical_nw = warnings_by_key.get("canonical_net_worth")
        if canonical_nw:
            apply_canonical_net_worth(parent_node, children_nodes, canonical_nw)
            parent_faqs = parent_node["faqs"]
            if include_religion:
                c1_faqs, c2_faqs = children_nodes[0]["faqs"], children_nodes[1]["faqs"]
            else:
                c2_faqs = children_nodes[0]["faqs"]
        canonical_zodiac = warnings_by_key.get("canonical_zodiac")
        if canonical_zodiac:
            apply_canonical_zodiac(parent_node, children_nodes, canonical_zodiac)
            parent_body = parent_node.get("body_content", parent_body)
            if include_religion:
                c1_body = children_nodes[0].get("body_content", c1_body)
                c2_body = children_nodes[1].get("body_content", c2_body)
            else:
                c2_body = children_nodes[0].get("body_content", c2_body)

        rs = not include_religion
        if include_religion:
            article_titles = {"parent": parent_title, "child_0": c1_title, "child_1": c2_title}
        else:
            article_titles = {"parent": parent_title, "child_0": c2_title}
        parent_links = await generate_internal_links(
            topic, "parent_overview", article_titles, research_context, religion_skipped=rs
        )
        await _blog_delay()
        c1_links: List[Dict[str, Any]] = []
        if include_religion:
            c1_links = await generate_internal_links(topic, "religion", article_titles, research_context, religion_skipped=False)
            await _blog_delay()
        c2_links = await generate_internal_links(
            topic, "net_worth", article_titles, research_context, religion_skipped=rs
        )

        base_path = f"/{parent_slug}"
        if include_religion:
            slug_map_rel = {"parent": base_path, "child_0": f"/{c1_slug}", "child_1": f"/{c2_slug}"}
        else:
            slug_map_rel = {"parent": base_path, "child_0": f"/{c2_slug}"}
        parent_body = inject_internal_links_into_body(parent_body, parent_links, slug_map_rel, religion_skipped=rs)
        if include_religion:
            c1_body = inject_internal_links_into_body(c1_body, c1_links, slug_map_rel, religion_skipped=False)
        c2_body = inject_internal_links_into_body(c2_body, c2_links, slug_map_rel, religion_skipped=rs)
        c2_body = sanitize_net_worth_body(c2_body)
        parent_body = _sanitize_conclusion_and_sources(parent_body)
        if include_religion:
            c1_body = _sanitize_conclusion_and_sources(c1_body)
        c2_body = _sanitize_conclusion_and_sources(c2_body)
        parent_body = rewrite_legacy_person_tree_paths(parent_body)
        if include_religion:
            c1_body = rewrite_legacy_person_tree_paths(c1_body)
        c2_body = rewrite_legacy_person_tree_paths(c2_body)

        breadcrumb_parent = [
            {"name": "Celebrities", "url": "/category/celebrities"},
            {"name": topic, "url": base_path},
        ]
        breadcrumb_c1 = breadcrumb_parent + [{"name": "Religion", "url": f"/{c1_slug}"}]
        breadcrumb_c2 = breadcrumb_parent + [{"name": "Net Worth", "url": f"/{c2_slug}"}]

        parent_article = {"topic": topic, "title": parent_title, "meta_description": parent_meta, "body_content": parent_body, "url": ""}
        parent_schema = await generate_schema_markup(
            "parent_overview", research_context, parent_article,
            faqs=parent_faqs, breadcrumb_items=breadcrumb_parent,
        )
        await _blog_delay()
        c1_schema: Optional[Dict[str, Any]] = None
        if include_religion:
            c1_article = {"topic": topic, "title": c1_title, "meta_description": c1_meta, "body_content": c1_body, "url": ""}
            c1_schema = await generate_schema_markup(
                "religion", research_context, c1_article,
                faqs=c1_faqs, breadcrumb_items=breadcrumb_c1,
            )
            await _blog_delay()
        c2_article = {"topic": topic, "title": c2_title, "meta_description": c2_meta, "body_content": c2_body, "url": ""}
        c2_schema = await generate_schema_markup(
            "net_worth", research_context, c2_article,
            faqs=c2_faqs, breadcrumb_items=breadcrumb_c2,
        )

        children_out: List[Dict[str, Any]] = []
        if include_religion:
            children_out = [
                {
                    "intent": "religion",
                    "title": c1_title,
                    "meta_description": c1_meta,
                    "body_content": c1_body,
                    "faqs": c1_faqs,
                    "related_queries": c1_queries,
                    "internal_links": c1_links,
                    "schema_markup": c1_schema,
                    "slug": c1_slug,
                },
                {
                    "intent": "net_worth",
                    "title": c2_title,
                    "meta_description": c2_meta,
                    "body_content": c2_body,
                    "faqs": c2_faqs,
                    "related_queries": c2_queries,
                    "internal_links": c2_links,
                    "schema_markup": c2_schema,
                    "slug": c2_slug,
                },
            ]
        else:
            children_out = [
                {
                    "intent": "net_worth",
                    "title": c2_title,
                    "meta_description": c2_meta,
                    "body_content": c2_body,
                    "faqs": c2_faqs,
                    "related_queries": c2_queries,
                    "internal_links": c2_links,
                    "schema_markup": c2_schema,
                    "slug": c2_slug,
                },
            ]

        state_data = {
            "is_tree": True,
            "religion_skipped": not include_religion,
            "topic": topic,
            "description": description,
            "keywords": keywords,
            "category": category,
            "title": parent_title,
            "slug": parent_slug,
            "focus_keyword": focus_keyword,
            "parent": {
                "title": parent_title,
                "meta_description": parent_meta,
                "body_content": parent_body,
                "faqs": parent_faqs,
                "related_queries": parent_queries,
                "internal_links": parent_links,
                "schema_markup": parent_schema,
                "slug": parent_slug,
            },
            "children": children_out,
        }
        if keyword_strategy:
            state_data["keyword_strategy"] = keyword_strategy
        if research_context:
            state_data["research_summary"] = research_context[:500] + ("..." if len(research_context) > 500 else "")
        workflow_state.state_data = state_data
        workflow_state.status = WorkflowStatus.COMPLETED
        db.commit()

    except Exception as e:
        workflow_state = get_workflow_state(db, workflow_id, user_id)
        if workflow_state:
            workflow_state.status = WorkflowStatus.FAILED
            workflow_state.error_message = str(e)
            db.commit()


@router.get("/youtube-interviews")
async def get_youtube_interviews(
    celebrity: str,
    current_user: User = Depends(get_current_user),
):
    """
    Search YouTube for interview videos of the given celebrity using Tavily.
    Requires TAVILY_API_KEY to be set.
    """
    if not celebrity or not celebrity.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'celebrity' is required",
        )
    try:
        videos = await search_youtube_interviews(celebrity.strip())
        return {"celebrity": celebrity.strip(), "videos": videos}
    except Exception as e:
        logger.exception("YouTube interviews search failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="YouTube interview search failed",
        ) from e


@router.post("/generate", response_model=BlogGenerateResponse)
async def generate_blog(
    request: BlogGenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start blog generation workflow with SEO-optimized content"""
    try:
        if (
            request.celebrity_content_type
            and request.category != "Celebrities & Public Figures"
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="celebrity_content_type is only valid when category is 'Celebrities & Public Figures'",
            )

        # Create workflow state
        from app.models.workflow_state import WorkflowState
        workflow_state = WorkflowState(
            user_id=current_user.id,
            workflow_type=WorkflowType.BLOG,
            status=WorkflowStatus.PENDING,
            state_data={
                "topic": request.topic,
                "description": request.description,
                "keywords": request.keywords or [],
                "category": request.category,
                **(
                    {"celebrity_content_type": request.celebrity_content_type}
                    if request.celebrity_content_type
                    else {}
                ),
                **({"suggested_title": request.suggested_title} if request.suggested_title else {}),
                **({"suggested_meta_description": request.suggested_meta_description} if request.suggested_meta_description else {}),
                **({"news_context": request.news_context} if request.news_context else {}),
            }
        )
        db.add(workflow_state)
        db.commit()
        db.refresh(workflow_state)

        # Run workflow in background
        background_tasks.add_task(
            run_blog_workflow,
            db,
            workflow_state.id,
            request.topic,
            request.description,
            request.keywords or [],
            request.category,
            current_user.id,
            request.include_research,
            request.celebrity_content_type,
            request.suggested_title,
            request.suggested_meta_description,
            request.news_context,
        )
        
        return BlogGenerateResponse(
            workflow_id=workflow_state.id,
            status="pending",
            message="Blog generation started"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting blog generation: {str(e)}"
        )


@router.get("/workflows", response_model=BlogWorkflowListResponse)
async def list_blog_workflows(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List current user's blog workflows (completed or not), newest first."""
    rows = (
        db.query(WorkflowState)
        .filter(
            WorkflowState.user_id == current_user.id,
            WorkflowState.workflow_type == WorkflowType.BLOG,
        )
        .order_by(WorkflowState.created_at.desc())
        .limit(limit)
        .all()
    )
    workflows = []
    for w in rows:
        state = w.state_data or {}
        workflows.append(
            BlogWorkflowListItem(
                id=w.id,
                status=w.status.value,
                created_at=w.created_at.isoformat(),
                topic=state.get("topic"),
                title=state.get("title"),
                is_tree=state.get("is_tree"),
                religion_skipped=state.get("religion_skipped"),
            )
        )
    return BlogWorkflowListResponse(workflows=workflows)


@router.get("/workflows/{workflow_id}", response_model=BlogWorkflowStatus)
async def get_workflow_status(
    workflow_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get workflow status"""
    workflow_state = get_workflow_state(db, workflow_id, current_user.id)
    if not workflow_state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    
    return BlogWorkflowStatus(
        id=workflow_state.id,
        status=workflow_state.status.value,
        state_data=_state_data_for_api(workflow_state.state_data),
        error_message=workflow_state.error_message,
        created_at=workflow_state.created_at.isoformat(),
        updated_at=workflow_state.updated_at.isoformat() if workflow_state.updated_at else workflow_state.created_at.isoformat()
    )


@router.patch("/workflows/{workflow_id}", response_model=BlogWorkflowStatus)
async def update_blog_workflow(
    workflow_id: int,
    request: BlogUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Save edited blog content to workflow state."""
    workflow_state = get_workflow_state(db, workflow_id, current_user.id)
    if not workflow_state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    if workflow_state.status != WorkflowStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only save content for completed workflows"
        )
    state_data = workflow_state.state_data or {}
    body_content = sanitize_blog_body(
        rewrite_legacy_person_tree_paths(request.body_content)
    )
    if state_data.get("is_tree") and request.blog_key:
        key = request.blog_key
        if key == "parent":
            if "parent" not in state_data:
                state_data["parent"] = {}
            state_data["parent"]["title"] = request.title
            state_data["parent"]["meta_description"] = request.meta_description
            state_data["parent"]["body_content"] = body_content
            state_data["parent"]["faqs"] = request.faqs
            state_data["parent"]["related_queries"] = request.related_queries
            if request.featured_image is not None:
                state_data["parent"]["featured_image"] = request.featured_image
            state_data["title"] = request.title
            if request.slug is not None:
                norm = _normalize_user_slug(request.slug, request.title)
                state_data["slug"] = norm
                state_data["parent"]["slug"] = norm
        elif key in ("child_0", "child_1"):
            idx = 0 if key == "child_0" else 1
            children = state_data.get("children") or []
            if idx < len(children):
                children[idx]["title"] = request.title
                children[idx]["meta_description"] = request.meta_description
                children[idx]["body_content"] = body_content
                children[idx]["faqs"] = request.faqs
                children[idx]["related_queries"] = request.related_queries
                if request.featured_image is not None:
                    children[idx]["featured_image"] = request.featured_image
                if request.slug is not None:
                    children[idx]["slug"] = _normalize_user_slug(request.slug, request.title)
    else:
        state_data["title"] = request.title
        state_data["meta_description"] = request.meta_description
        state_data["body_content"] = body_content
        state_data["faqs"] = request.faqs
        state_data["related_queries"] = request.related_queries
        if request.featured_image is not None:
            state_data["featured_image"] = request.featured_image
        if request.slug is not None:
            state_data["slug"] = _normalize_user_slug(request.slug, request.title)
    workflow_state.state_data = state_data
    flag_modified(workflow_state, "state_data")
    db.commit()
    db.refresh(workflow_state)
    return BlogWorkflowStatus(
        id=workflow_state.id,
        status=workflow_state.status.value,
        state_data=_state_data_for_api(workflow_state.state_data),
        error_message=workflow_state.error_message,
        created_at=workflow_state.created_at.isoformat(),
        updated_at=workflow_state.updated_at.isoformat() if workflow_state.updated_at else workflow_state.created_at.isoformat()
    )


def _derive_featured_image_keyword(title: str) -> str:
    """Derive search keyword from blog title (e.g. 'Who Is Elina Svitolina? 5 Facts' -> 'Elina Svitolina')."""
    if not title or not title.strip():
        return "featured"
    t = title.strip()
    for prefix in ("Who Is ", "Who is ", "What Is ", "What is ", "Who Are ", "Who are "):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    # Take part before ? or : or first 50 chars
    for sep in ("?", ":", "–", "-"):
        if sep in t:
            t = t.split(sep)[0].strip()
    t = re.sub(r"\s+", " ", t)[:50].strip()
    return t or "featured"


def _seo_alt_from_title(title: str) -> str:
    """Build SEO-friendly alt text from blog title (max ~100 chars)."""
    if not title or not title.strip():
        return "Featured image"
    t = re.sub(r"\s+", " ", title.strip())[:100]
    return t or "Featured image"


def _image_data_to_results(images_data: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Convert raw image dicts to list of dicts for image search history storage."""
    return [
        {
            "title": img.get("title", ""),
            "link": img.get("link", ""),
            "original": img.get("original", ""),
            "thumbnail": img.get("thumbnail", ""),
            "source": img.get("source", ""),
        }
        for img in images_data
    ]


def _get_images_from_history(
    db: Session, user_id: int, keyword: str
) -> List[Dict[str, Any]]:
    """Return most recent image search results for this user and keyword, or empty list."""
    row = (
        db.query(ImageSearchHistory)
        .filter(
            ImageSearchHistory.user_id == user_id,
            ImageSearchHistory.keyword == keyword,
        )
        .order_by(ImageSearchHistory.created_at.desc())
        .first()
    )
    if not row or not row.results:
        return []
    # Stored results have same keys as SerpAPI: title, link, original, thumbnail, source
    return list(row.results)


@router.post("/workflows/{workflow_id}/suggest-featured")
async def suggest_featured_image(
    workflow_id: int,
    body: Optional[Dict[str, Any]] = Body(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Suggest a featured image for a blog article: search images by keyword (from title),
    take first 10 (excluding images already used by other articles in the same workflow),
    LLM picks one. Returns { url, thumbnail, title, source, alt }.
    """
    workflow_state = get_workflow_state(db, workflow_id, current_user.id)
    if not workflow_state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    if workflow_state.status != WorkflowStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only suggest featured image for completed workflows",
        )
    payload = body or {}
    blog_key = payload.get("blog_key", "parent")
    state_data = workflow_state.state_data or {}

    # Resolve article (title, meta_description) and collect used image URLs from other articles
    is_tree = state_data.get("is_tree", False)
    used_urls = set()
    if is_tree and blog_key in ("parent", "child_0", "child_1"):
        if blog_key == "parent":
            article = state_data.get("parent") or {}
            for c in (state_data.get("children") or [])[:2]:
                fi = (c or {}).get("featured_image") or {}
                if fi.get("url"):
                    used_urls.add((fi.get("url") or "").strip())
        else:
            idx = 0 if blog_key == "child_0" else 1
            children = state_data.get("children") or []
            article = (children[idx] if idx < len(children) else None) or {}
            parent_fi = (state_data.get("parent") or {}).get("featured_image") or {}
            if parent_fi.get("url"):
                used_urls.add((parent_fi.get("url") or "").strip())
            other_idx = 1 - idx
            if other_idx < len(children) and children[other_idx]:
                other_fi = (children[other_idx].get("featured_image") or {}).get("url")
                if other_fi:
                    used_urls.add((other_fi or "").strip())
    else:
        article = state_data
        blog_key = "parent"

    title = (article.get("title") or state_data.get("title") or "").strip()
    meta = (article.get("meta_description") or "").strip()

    # Tree workflow: ensure image searches are based on the core entity (person/topic),
    # not the child-article intent like "religion" or "net worth". This prevents
    # generating separate image-history buckets for each child title.
    keyword: str
    if is_tree:
        base = (state_data.get("focus_keyword") or state_data.get("topic") or "").strip()
        keyword = _derive_featured_image_keyword(base) if base else _derive_featured_image_keyword(title)
    else:
        keyword = _derive_featured_image_keyword(title)

    # 1) Try database first: reuse previous image search for this keyword if available
    images_data = _get_images_from_history(db, current_user.id, keyword)

    # 2) If no results from DB, call SerpAPI and save to history
    if not images_data:
        try:
            images_data = await serpapi_search_images(keyword, 100)
        except Exception as e:
            logger.warning("Image search failed for suggest-featured: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Image search failed: {str(e)}",
            )
        try:
            record = ImageSearchHistory(
                user_id=current_user.id,
                keyword=keyword,
                results=_image_data_to_results(images_data),
            )
            db.add(record)
            db.commit()
        except Exception as e:
            logger.warning("Could not save suggest-featured search to image history: %s", e)

    # Filter out already-used URLs (compare by original and link)
    candidates = []
    for img in images_data:
        orig = (img.get("original") or "").strip()
        link = (img.get("link") or "").strip()
        if orig and orig not in used_urls and link not in used_urls:
            candidates.append(img)
        if len(candidates) >= 10:
            break
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No unused images found; clear featured images from other articles or try again.",
        )
    idx = await select_featured_image(title, meta, candidates)
    chosen = candidates[min(idx, len(candidates) - 1)]
    alt = _seo_alt_from_title(title)
    return {
        "url": chosen.get("original") or chosen.get("link") or "",
        "thumbnail": chosen.get("thumbnail") or chosen.get("original") or "",
        "title": chosen.get("title") or "",
        "source": chosen.get("source") or "",
        "alt": alt,
    }


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blog_workflow(
    workflow_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a blog workflow and its saved content."""
    workflow_state = get_workflow_state(db, workflow_id, current_user.id)
    if not workflow_state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    if workflow_state.workflow_type != WorkflowType.BLOG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not a blog workflow"
        )
    db.delete(workflow_state)
    db.commit()
    return None


@router.post("/publish", response_model=BlogPublishResponse)
async def publish_blog(
    request: BlogPublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Publish generated blog to WordPress.

    - Prevents duplicate publishing (same workflow + blog_key + site).
    - Records every publish in the `published_posts` table for tracking.
    """
    try:
        # Get workflow state
        workflow_state = get_workflow_state(db, request.workflow_id, current_user.id)
        if not workflow_state:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found"
            )

        if workflow_state.status != WorkflowStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workflow not completed"
            )

        # Get WordPress site
        site = get_wordpress_site(db, request.wordpress_site_id, current_user.id)
        if not site:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="WordPress site not found"
            )

        blog_key = request.blog_key or "parent"

        # --- Duplicate prevention via published_posts table ---
        existing = db.query(PublishedPost).filter(
            PublishedPost.workflow_id == request.workflow_id,
            PublishedPost.blog_key == blog_key,
            PublishedPost.wordpress_site_id == request.wordpress_site_id,
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Already published (WP Post #{existing.wp_post_id})",
            )

        # Get content from workflow state (tree: parent or child by blog_key)
        state_data = workflow_state.state_data or {}
        is_tree = state_data.get("is_tree")

        featured_image_for_publish = None
        publish_slug: Optional[str] = None
        publish_focus_keyword: Optional[str] = None
        publish_seo_title: Optional[str] = None
        publish_meta_description: Optional[str] = None
        if is_tree and blog_key in ("child_0", "child_1"):
            idx = 0 if blog_key == "child_0" else 1
            children = state_data.get("children") or []
            if idx >= len(children):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid blog_key for tree")
            article = children[idx]
            title = article.get("title", "Untitled")
            content = article.get("body_content") or ""
            internal_links = article.get("internal_links") or []
            schema_markup = article.get("schema_markup")
            faqs = article.get("faqs") or []
            related_queries = article.get("related_queries") or []
            featured_image_for_publish = article.get("featured_image")
            parent_slug = state_data.get("slug", "")
            _ch_intent = (article.get("intent") or "").strip()
            publish_slug = coalesce_tree_child_slug(
                (parent_slug or "").strip(),
                article.get("slug"),
                blog_key,
                child_intent=_ch_intent if _ch_intent in ("religion", "net_worth") else None,
            )
            publish_focus_keyword = state_data.get("focus_keyword")
            publish_seo_title = title
            publish_meta_description = article.get("meta_description")
        else:
            if is_tree:
                article = state_data.get("parent") or {}
                title = article.get("title", state_data.get("title", "Untitled"))
                content = article.get("body_content") or ""
                internal_links = article.get("internal_links") or []
                schema_markup = article.get("schema_markup")
                faqs = article.get("faqs") or []
                related_queries = article.get("related_queries") or []
                featured_image_for_publish = article.get("featured_image")
                parent_slug = state_data.get("slug", "")
                publish_slug = (parent_slug or None) if (parent_slug and str(parent_slug).strip()) else None
                publish_focus_keyword = state_data.get("focus_keyword")
                publish_seo_title = title
                publish_meta_description = article.get("meta_description")
            else:
                title = state_data.get("title", "Untitled")
                content = state_data.get("body_content") or state_data.get("formatted_content") or state_data.get("content", "")
                internal_links = []
                schema_markup = None
                faqs = state_data.get("faqs") or []
                related_queries = state_data.get("related_queries") or []
                featured_image_for_publish = state_data.get("featured_image")
                publish_slug = state_data.get("slug")
                publish_focus_keyword = state_data.get("focus_keyword")
                publish_seo_title = title
                publish_meta_description = state_data.get("meta_description")

        base_url = (site.url or "").rstrip("/")
        if is_tree:
            ps = (state_data.get("slug") or "").strip()
            ch = state_data.get("children") or []
            path_prefix = f"{base_url}/{ps}" if ps else base_url
            if state_data.get("religion_skipped") and len(ch) >= 1:
                _i0 = (ch[0].get("intent") or "").strip()
                s0 = coalesce_tree_child_slug(
                    ps,
                    ch[0].get("slug") if ch else None,
                    "child_0",
                    child_intent=_i0 if _i0 in ("religion", "net_worth") else "net_worth",
                )
                slug_map = {
                    "parent": path_prefix,
                    "child_0": f"{base_url}/{s0}",
                }
            else:
                s0 = coalesce_tree_child_slug(ps, ch[0].get("slug") if len(ch) > 0 else None, "child_0")
                s1 = coalesce_tree_child_slug(ps, ch[1].get("slug") if len(ch) > 1 else None, "child_1")
                slug_map = {
                    "parent": path_prefix,
                    "child_0": f"{base_url}/{s0}",
                    "child_1": f"{base_url}/{s1}",
                }
        else:
            path_prefix = base_url
            slug_map = {
                "parent": path_prefix,
                "child_0": f"{path_prefix}/religion",
                "child_1": f"{path_prefix}/net-worth",
            }
        content = sanitize_blog_body(content or "")
        use_html = bool(request.body_content_html and request.body_content_html.strip())

        if use_html:
            # Single HTML pipeline: frontend sent body as HTML (same as preview); append FAQs, related queries, related articles, schema
            import html as html_module
            content = rewrite_legacy_person_tree_paths(request.body_content_html.strip())
            faqs_queries_html = _render_faqs_and_queries_html(faqs, related_queries)
            if faqs_queries_html:
                content = content + "\n\n" + faqs_queries_html
            if internal_links:
                related_items = "".join(
                    f'<li><a href="{slug_map.get(link.get("target", "parent"), path_prefix)}">'
                    f"{html_module.escape(link.get('anchor_text') or 'read more')}</a></li>"
                    for link in internal_links
                )
                content = content + '\n\n<h2 id="related-articles">Related articles</h2>\n<ul>' + related_items + "</ul>"
            if schema_markup:
                import json
                content = content + f'\n\n<script type="application/ld+json">\n{json.dumps(schema_markup)}\n</script>'
            result = await publish_to_wordpress(
                site, title, content, request.status, featured_image=featured_image_for_publish, content_is_html=True,
                focus_keyword=publish_focus_keyword, slug=publish_slug,
                seo_title=publish_seo_title, meta_description=publish_meta_description
            )
        else:
            # Fallback: get content from state, append FAQs, related queries, related articles, schema as markdown
            content = rewrite_legacy_person_tree_paths(content or "")
            faqs_queries_md = _render_faqs_and_queries_markdown(faqs, related_queries)
            if faqs_queries_md:
                content = content + faqs_queries_md
            if internal_links:
                related_lines = ["\n\n## Related articles\n"]
                for link in internal_links:
                    target = link.get("target", "parent")
                    href = slug_map.get(target, path_prefix)
                    anchor = link.get("anchor_text") or "read more"
                    related_lines.append(f"- [{anchor}]({href})")
                content = content + "\n".join(related_lines)
            if schema_markup:
                import json
                content = content + f'\n\n<script type="application/ld+json">\n{json.dumps(schema_markup)}\n</script>'
            result = await publish_to_wordpress(
                site, title, content, request.status, featured_image=featured_image_for_publish, content_is_html=False,
                focus_keyword=publish_focus_keyword, slug=publish_slug,
                seo_title=publish_seo_title, meta_description=publish_meta_description
            )

        if result.get("success"):
            # Record in published_posts table
            record = PublishedPost(
                user_id=current_user.id,
                workflow_id=request.workflow_id,
                wordpress_site_id=request.wordpress_site_id,
                blog_key=blog_key,
                wp_post_id=result["post_id"],
                wp_link=result.get("link"),
                wp_status=request.status,
                title=title,
            )
            db.add(record)

            # Also keep legacy state_data fields for backward compatibility
            if is_tree and blog_key in ("child_0", "child_1"):
                if "wordpress_post_ids" not in state_data:
                    state_data["wordpress_post_ids"] = {}
                state_data["wordpress_post_ids"][blog_key] = result.get("post_id")
            else:
                state_data["wordpress_post_id"] = result.get("post_id")
            workflow_state.state_data = state_data
            db.commit()

        return BlogPublishResponse(
            success=result.get("success", False),
            message=result.get("message", ""),
            post_id=result.get("post_id"),
            link=result.get("link")
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error publishing blog: {str(e)}"
        )


@router.post("/unpublish", response_model=BlogUnpublishResponse)
async def unpublish_blog(
    request: BlogUnpublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Unpublish: delete the post on WordPress and remove the published_posts record."""
    record = (
        db.query(PublishedPost)
        .filter(
            PublishedPost.id == request.published_post_id,
            PublishedPost.user_id == current_user.id,
        )
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Published post not found",
        )

    site = get_wordpress_site(db, record.wordpress_site_id, current_user.id)
    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress site not found",
        )

    result = await delete_post_from_wordpress(site, record.wp_post_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("message", "Failed to delete post on WordPress"),
        )

    workflow_state = get_workflow_state(db, record.workflow_id, current_user.id)
    if workflow_state and workflow_state.state_data:
        state_data = workflow_state.state_data
        if state_data.get("is_tree") and record.blog_key in ("child_0", "child_1"):
            ids = state_data.get("wordpress_post_ids") or {}
            ids.pop(record.blog_key, None)
            state_data["wordpress_post_ids"] = ids
        else:
            state_data.pop("wordpress_post_id", None)
        workflow_state.state_data = state_data

    db.delete(record)
    db.commit()

    return BlogUnpublishResponse(success=True, message="Unpublished and deleted on WordPress")


@router.get("/workflows/{workflow_id}/publish-status", response_model=WorkflowPublishStatus)
async def get_publish_status(
    workflow_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get publish status for all articles in a workflow.

    Returns a list of published_posts records for this workflow,
    allowing the frontend to know which articles are already published,
    to which site, and the WordPress link.
    """
    workflow_state = get_workflow_state(db, workflow_id, current_user.id)
    if not workflow_state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )

    rows = (
        db.query(PublishedPost)
        .filter(PublishedPost.workflow_id == workflow_id, PublishedPost.user_id == current_user.id)
        .order_by(PublishedPost.published_at.asc())
        .all()
    )

    # Eager-load site names (already joined via relationship, but build response)
    site_cache: Dict[int, str] = {}
    posts = []
    for r in rows:
        if r.wordpress_site_id not in site_cache:
            s = get_wordpress_site(db, r.wordpress_site_id, current_user.id)
            site_cache[r.wordpress_site_id] = s.name if s else "Unknown"
        posts.append(PublishedPostItem(
            id=r.id,
            blog_key=r.blog_key,
            wordpress_site_id=r.wordpress_site_id,
            site_name=site_cache[r.wordpress_site_id],
            wp_post_id=r.wp_post_id,
            wp_link=r.wp_link,
            wp_status=r.wp_status,
            title=r.title,
            published_at=r.published_at.isoformat() if r.published_at else "",
        ))

    return WorkflowPublishStatus(workflow_id=workflow_id, posts=posts)

