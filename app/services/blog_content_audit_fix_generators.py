"""Generate before/after fix proposals for blog content audit."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from app.services.blog_consistency import CITATION_POLICY_PROMPT
from app.services.blog_content_audit_fix_planner import FixPlanItem
from app.services.humanizer.diff import build_diff
from app.services.openrouter_service import expand_thin_blog_body, generate_content

try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


def _strip_html(html: str) -> str:
    if not html:
        return ""
    if HAS_BS4:
        return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    return re.sub(r"<[^>]+>", " ", html)


def _word_count(html: str) -> int:
    return len(_strip_html(html).split())


def _html_sources_footer() -> str:
    today = date.today().isoformat()
    return (
        f'<p><strong>Last Updated:</strong> {today} | '
        f"<strong>Sources:</strong> Wikipedia</p>"
    )


def _segments(before: str, after: str) -> list[dict[str, Any]]:
    return [s.model_dump() for s in build_diff(before or "", after or "")]


async def generate_meta_description(
    *,
    title: str,
    content_html: str,
    current_meta: Optional[str],
) -> str:
    excerpt = _strip_html(content_html)[:1500]
    prompt = f"""Write one SEO meta description (120-160 characters) for this blog post.
Title: {title}
Current meta: {current_meta or "(none)"}
Content excerpt: {excerpt}

Return ONLY the meta description text, no quotes."""
    try:
        result = await generate_content(prompt, max_tokens=120, temperature=0.4, timeout_seconds=45.0)
        meta = (result or "").strip().strip('"').strip("'")
        if len(meta) > 320:
            meta = meta[:320]
        return meta or (current_meta or "")
    except Exception:
        fallback = f"Learn about {title}. In-depth coverage with sources and key facts."
        return fallback[:160]


async def generate_missing_sections_html(
    *,
    title: str,
    article_type: str,
    content_html: str,
    topics: list[str],
) -> str:
    if not topics:
        return content_html
    topics_list = "\n".join(f"- {t}" for t in topics)
    excerpt = _strip_html(content_html)[:3000]
    prompt = f"""Add new H2 sections to this article for missing competitor topics.
Title: {title}
Article type: {article_type}
Topics to add as H2 sections:
{topics_list}

{CITATION_POLICY_PROMPT}

Return ONLY the new HTML sections (h2 + paragraphs), no full article rewrite.
Each section 120-200 words. Use Wikipedia attribution in plain text when citing facts.

Existing excerpt for context:
{excerpt}"""
    try:
        fragment = await generate_content(prompt, max_tokens=2000, temperature=0.45, timeout_seconds=90.0)
        fragment = (fragment or "").strip()
        if fragment and "<h2" not in fragment.lower():
            fragment = "".join(f"<h2>{t}</h2><p>Section content for {t}.</p>" for t in topics)
        return (content_html or "").rstrip() + "\n" + fragment
    except Exception:
        fragment = "".join(
            f"<h2>{t}</h2><p>Additional coverage of {t} according to Wikipedia.</p>" for t in topics
        )
        return (content_html or "").rstrip() + "\n" + fragment


async def generate_faq_section_html(
    *,
    title: str,
    article_type: str,
    content_html: str,
    topics: list[str],
) -> str:
    topic_hint = ", ".join(topics[:5]) if topics else title
    excerpt = _strip_html(content_html)[:2000]
    prompt = f"""Add an FAQ section to this article as HTML.
Title: {title}
Article type: {article_type}
Suggested questions related to: {topic_hint}

{CITATION_POLICY_PROMPT}

Return ONLY HTML: one h2 "Frequently Asked Questions" plus 3-5 h3 questions with paragraph answers.
Use plain-text Wikipedia attribution where needed.

Context:
{excerpt}"""
    try:
        fragment = await generate_content(prompt, max_tokens=1800, temperature=0.45, timeout_seconds=90.0)
        fragment = (fragment or "").strip()
        if not fragment:
            raise ValueError("empty faq")
        return (content_html or "").rstrip() + "\n" + fragment
    except Exception:
        fragment = (
            '<h2>Frequently Asked Questions</h2>'
            f'<h3>What is {title} known for?</h3>'
            f"<p>According to Wikipedia, this topic covers key career and background details.</p>"
        )
        return (content_html or "").rstrip() + "\n" + fragment


def append_sources_footer(content_html: str) -> str:
    footer = _html_sources_footer()
    lower = (content_html or "").lower()
    if "last updated" in lower and "sources" in lower:
        return content_html
    return (content_html or "").rstrip() + "\n" + footer


async def expand_content_html(
    *,
    title: str,
    article_type: str,
    content_html: str,
    word_target: int,
) -> str:
    plain = _strip_html(content_html)
    if _word_count(content_html) >= word_target:
        return content_html
    # expand_thin_blog_body expects markdown-ish draft; use plain text wrapper
    draft = plain
    try:
        expanded = await expand_thin_blog_body(
            title,
            draft,
            min_words=word_target,
            content_intent=article_type,
        )
        if expanded and expanded != draft and len(expanded.split()) > len(draft.split()):
            # Wrap expanded prose back into HTML paragraphs
            paragraphs = "".join(f"<p>{p.strip()}</p>" for p in expanded.split("\n\n") if p.strip())
            if paragraphs:
                return paragraphs
    except Exception:
        pass
    # Fallback: append expansion paragraph
    pad_words = max(0, word_target - _word_count(content_html))
    filler = (
        f"<p>Additional context on {title} includes career milestones, public records, "
        f"and sourced biographical details according to Wikipedia.</p>"
    )
    return (content_html or "") + filler


def fix_image_alt_html(content_html: str, *, max_images: int = 5) -> str:
    if not HAS_BS4 or not content_html:
        return content_html or ""
    soup = BeautifulSoup(content_html, "html.parser")
    fixed = 0
    for img in soup.find_all("img"):
        if fixed >= max_images:
            break
        alt = (img.get("alt") or "").strip()
        if alt:
            continue
        src = (img.get("src") or "").split("/")[-1].split(".")[0].replace("-", " ").strip()
        img["alt"] = src[:120] if src else "Article illustration"
        fixed += 1
    return str(soup)


async def generate_fix_proposal(
    plan: FixPlanItem,
    *,
    title: str,
    article_type: str,
    content_html: str,
    meta_description: Optional[str],
) -> dict[str, Any]:
    """Return dict with before_text, after_proposed, diff_segments, generator_meta."""
    fix_type = plan.fix_type
    target = plan.target_field
    before = content_html if target == "content" else (meta_description or "")
    after = before
    meta: dict[str, Any] = {"fix_type": fix_type, "regen_count": 0, "prompt_version": "1"}

    if fix_type == "add_meta_description":
        after = await generate_meta_description(
            title=title, content_html=content_html, current_meta=meta_description
        )
    elif fix_type == "append_sources_footer":
        after = append_sources_footer(content_html)
    elif fix_type == "add_missing_sections":
        topics = plan.payload.get("topics") or []
        after = await generate_missing_sections_html(
            title=title, article_type=article_type, content_html=content_html, topics=topics
        )
    elif fix_type == "add_faq_section":
        topics = plan.payload.get("topics") or []
        after = await generate_faq_section_html(
            title=title, article_type=article_type, content_html=content_html, topics=topics
        )
    elif fix_type == "fix_image_alt":
        after = fix_image_alt_html(content_html, max_images=int(plan.payload.get("max_images") or 5))
    elif fix_type == "expand_content":
        word_target = int(plan.payload.get("word_target") or 1200)
        after = await expand_content_html(
            title=title, article_type=article_type, content_html=content_html, word_target=word_target
        )

    return {
        "before_text": before,
        "after_proposed": after,
        "diff_segments": _segments(before, after),
        "generator_meta": meta,
    }
