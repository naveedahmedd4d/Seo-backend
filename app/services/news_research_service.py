"""Shared Google News fetch + social-style LLM artifacts for research and social post."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.serpapi_service import fetch_google_news_light
from app.services.openrouter_service import (
    generate_news_headline,
    generate_social_post_description,
    generate_social_post_hashtags,
    generate_social_post_story_description,
)

logger = logging.getLogger(__name__)


@dataclass
class NewsResearchBundle:
    query: str
    headline: str
    story_description: str
    description: str
    hashtags: List[str] = field(default_factory=list)
    news_results: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def normalized_headline(self) -> str:
        return normalize_news_headline(self.headline)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "headline": self.headline,
            "normalized_headline": self.normalized_headline,
            "story_description": self.story_description,
            "description": self.description,
            "hashtags": self.hashtags,
            "news_results": self.news_results,
        }


def normalize_news_headline(headline: str) -> str:
    """Join a 2-line social headline into a single blog H1 (strips ** highlight markers)."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", (headline or "").strip())
    if not text:
        return ""
    parts = [p.replace("\n", " ").strip() for p in text.split("\n") if p.strip()]
    if len(parts) == 1:
        return parts[0]
    if len(parts) >= 2:
        line1, line2 = parts[0], parts[1]
        if line1.endswith("—") or line1.endswith("-"):
            return f"{line1.rstrip('-—').strip()} — {line2}"
        return f"{line1} — {line2}"
    return text


def format_news_bundle_for_llm(bundle: NewsResearchBundle) -> str:
    """Structured news block for LLM prompts (citations + hook context)."""
    lines = [
        f"Query: {bundle.query}",
        f"Headline: {bundle.normalized_headline}",
        f"Story hook (≤100 words): {bundle.story_description}",
        f"Short description: {bundle.description}",
        "",
        "News sources (mention in plain text only — do not embed links; use Wikipedia if a citation is needed):",
    ]
    for i, item in enumerate(bundle.news_results[:7], 1):
        title = (item.get("title") or "").strip()
        source = (item.get("source") or "").strip()
        snippet = (item.get("snippet") or "").strip()[:280]
        date = (item.get("date") or "").strip()
        link = (item.get("link") or "").strip()
        lines.append(
            f"{i}. [{title}]({link}) — {source} ({date})\n   {snippet}"
            if link
            else f"{i}. {title} — {source} ({date})\n   {snippet}"
        )
    if bundle.hashtags:
        lines.append("")
        lines.append("Related tags: " + ", ".join(bundle.hashtags[:12]))
    return "\n".join(lines)


async def build_news_bundle(
    query: str,
    *,
    num_results: int = 7,
    max_days: int = 7,
    hashtag_count: int = 12,
    template_variant: Optional[str] = None,
    serpapi_api_key: Optional[str] = None,
) -> Optional[NewsResearchBundle]:
    """
    Fetch Google News and generate headline, story caption, short description, hashtags.
    Returns None on failure (caller should degrade gracefully).
    """
    q = (query or "").strip()
    if not q:
        return None
    try:
        news_results = await fetch_google_news_light(
            q,
            num_results=num_results,
            max_days=max_days,
            api_key=serpapi_api_key,
        )
        if not news_results:
            logger.info("No news results for query: %s", q)
            return None

        headline = await generate_news_headline(
            news_results, q, template_variant=template_variant
        )
        description = await generate_social_post_description(
            news_results, headline=headline, query=q
        )
        story_description = await generate_social_post_story_description(
            news_results, headline=headline, query=q
        )
        hashtags = await generate_social_post_hashtags(
            news_results, headline=headline, query=q, count=hashtag_count
        )
        return NewsResearchBundle(
            query=q,
            headline=headline,
            story_description=story_description,
            description=description,
            hashtags=hashtags or [],
            news_results=news_results,
        )
    except Exception as e:
        logger.warning("build_news_bundle failed for %s: %s", q, e)
        return None


def estimate_engagement_score(headline: str) -> str:
    """Heuristic engagement label for social post metadata."""
    word_count = len(headline.replace("\n", " ").split())
    char_count = len(headline)
    engagement_keywords = [
        "breaking", "shocking", "debate", "fans", "exclusive", "reveals",
    ]
    headline_lower = headline.lower()
    if any(kw in headline_lower for kw in engagement_keywords):
        return "High"
    if word_count < 10 or char_count < 60:
        return "Low"
    return "Medium"


def suggested_platforms_for_headline(headline: str) -> List[str]:
    char_count = len(headline)
    platforms = ["Twitter/X", "Instagram", "Facebook"]
    if char_count < 140:
        return ["Twitter/X (optimal)"] + [p for p in platforms if p != "Twitter/X"]
    return platforms


# Second-segment phrases that signal a biography SEO title (not a social reaction line).
BIOGRAPHY_HOOK_PHRASES = (
    "biography",
    "life story",
    "who is",
    "who was",
    "net worth",
    "untold facts",
    "career highlights",
    "family &",
    "religion",
    "age,",
    "full life",
    "career &",
    "life &",
    "career, fame",
)


def get_biography_title_hook_variants(year: Optional[int] = None) -> List[str]:
    """Curated biography hooks — vary the ending across articles."""
    from datetime import date

    y = year or date.today().year
    return [
        "Her Full Life Story",
        "His Full Life Story",
        "Biography, Family & Net Worth",
        "Who Is He Really?",
        "Who Is She Really?",
        f"Age, Religion, Net Worth & More",
        "Full Biography & Untold Facts",
        f"Biography, Age & Net Worth {y}",
        f"Biography, Career & Net Worth {y}",
        "Life Story, Career & Fame",
        f"Net Worth, Age & Biography {y}",
    ]


def pick_biography_title_hook(name: str, year: Optional[int] = None) -> str:
    """Stable per-person hook so the same celebrity gets a consistent style; differs across people."""
    variants = get_biography_title_hook_variants(year)
    key = (name or "celebrity").strip().lower()
    idx = sum(ord(c) for c in key) % len(variants)
    return variants[idx]


def biography_title_hook(year: Optional[int] = None, name: str = "") -> str:
    """Biography hook for title second segment (varies by name when provided)."""
    if (name or "").strip():
        return pick_biography_title_hook(name, year)
    from datetime import date

    y = year or date.today().year
    return f"Biography, Age & Net Worth {y}"


def format_biography_hook_options_for_prompt(year: Optional[int] = None) -> str:
    return "\n".join(f"- {h}" for h in get_biography_title_hook_variants(year))


def _title_second_segment(title: str) -> Optional[str]:
    t = (title or "").strip()
    if re.search(r"\s[—–-]\s", t):
        return re.split(r"\s[—–-]\s", t, maxsplit=1)[1].strip()
    return None


def has_biography_hook_tail(title: str) -> bool:
    """True if title has a biography-style second segment (or clear bio intent)."""
    t = (title or "").strip()
    if not t:
        return False
    seg = _title_second_segment(t)
    if seg:
        lower = seg.lower()
        return any(p in lower for p in BIOGRAPHY_HOOK_PHRASES)
    lower = t.lower()
    return any(p in lower for p in BIOGRAPHY_HOOK_PHRASES)


def is_weak_biography_title(title: str, name: str) -> bool:
    """True when title reads like a vague social headline, not a biography SEO title."""
    t = (title or "").strip()
    if not t:
        return True
    lower = t.lower()
    first = (name or "").strip().split()[0].lower() if name else ""
    if has_biography_hook_tail(t) and first and first in lower[: max(len(first) + 25, 35)]:
        return False
    vague_tail = (
        "fans praise",
        "fans react",
        "fans and analysts",
        "analysts debate",
        "debate the",
        "power surge",
        "stuns fans",
    )
    if any(v in lower for v in vague_tail) and not has_biography_hook_tail(t):
        return True
    if _title_second_segment(t) and not has_biography_hook_tail(t):
        return True
    return not has_biography_hook_tail(t)


BIOGRAPHY_META_ACTION_WORDS = ("learn", "discover", "explore", "find out")
BIOGRAPHY_META_REQUIRED_KEYWORDS = ("biography", "age", "net worth", "religion")


def is_weak_biography_meta(
    meta: str,
    title: str,
    year: Optional[int] = None,
) -> bool:
    """True when meta is too short, missing SEO keywords, or copies the title."""
    from datetime import date

    y = str(year or date.today().year)
    m = " ".join((meta or "").split()).strip()
    if len(m) < 145:
        return True
    lower = m.lower()
    for kw in BIOGRAPHY_META_REQUIRED_KEYWORDS:
        if kw not in lower:
            return True
    if not any(w in lower for w in BIOGRAPHY_META_ACTION_WORDS):
        return True
    if y not in m:
        return True
    title_l = " ".join((title or "").split()).lower()
    if title_l and m.lower() == title_l[: len(m)]:
        return True
    if title_l and len(title_l) > 20 and m.lower() in title_l:
        return True
    return False


def clamp_biography_meta_description(
    text: str,
    year: Optional[int] = None,
    *,
    min_len: int = 150,
    max_len: int = 160,
) -> str:
    """Trim or lightly extend meta to Rank Math 150–160 character band."""
    from datetime import date

    y = year or date.today().year
    t = " ".join((text or "").split()).strip()
    if not t:
        t = (
            f"Discover the full biography, age, net worth, and religion for this star in {y}. "
            f"Learn career highlights and personal facts."
        )
    if str(y) not in t:
        t = f"{t.rstrip('.')} ({y})."
    lower = t.lower()
    if not any(w in lower for w in BIOGRAPHY_META_ACTION_WORDS):
        t = f"Discover {t[0].lower()}{t[1:]}" if t else "Discover their story."
    for kw in BIOGRAPHY_META_REQUIRED_KEYWORDS:
        if kw not in t.lower():
            t = f"{t.rstrip('.')}. Covers {kw}."
    while len(t) < min_len:
        extra = f" Learn biography, age, net worth, religion, and career facts for {y}."
        if len(t) + len(extra) > max_len:
            break
        t = t.rstrip(".") + extra
        if len(t) >= min_len:
            break
        t = f"{t} Full guide."
    if len(t) > max_len:
        cut = t[: max_len - 3]
        if " " in cut[max(0, len(cut) - 40) :]:
            cut = cut.rsplit(" ", 1)[0]
        t = cut.rstrip(".,;— ") + "..."
    if len(t) > max_len:
        t = t[:max_len].rstrip()
    return t


def build_fallback_biography_meta_description(
    name: str,
    seo_title: str,
    year: Optional[int] = None,
) -> str:
    """Rule-based meta when LLM is unavailable (150–160 chars, keywords + year)."""
    from datetime import date

    y = year or date.today().year
    first = (name or "This celebrity").strip()
    draft = (
        f"Discover who {first} is in {y}. Explore biography, age, net worth, religion, "
        f"and career highlights beyond today's headlines. Learn key facts and background."
    )
    return clamp_biography_meta_description(draft, y)


def ensure_biography_seo_title(title: str, name: str, year: Optional[int] = None) -> str:
    """Append a varied biography SEO hook if missing; keep specific news in the first segment."""
    t = (title or "").strip()
    hook = pick_biography_title_hook(name, year)
    if not t:
        return f"Who Is {name}? — {hook}"
    if not is_weak_biography_title(t, name):
        return t
    if re.search(r"\s[—–-]\s", t):
        first = re.split(r"\s[—–-]\s", t, maxsplit=1)[0].strip()
        if name and name.split()[0].lower() not in first.lower():
            first = f"{name} {first[0].lower() + first[1:]}" if first else name
        return f"{first} — {hook}"
    if name and name.lower() not in t.lower()[: len(name) + 5]:
        t = f"{name} {t[0].lower() + t[1:]}" if t else name
    return f"{t} — {hook}"
