"""Per-platform caption builders for social post publishing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


INSTAGRAM_MAX = 2200
X_MAX = 280


@dataclass
class PublishCopyContext:
    headline: str
    short_description: Optional[str]
    story_caption: Optional[str]
    hashtags: list[str]
    source_url: Optional[str]


def _hashtag_line(hashtags: list[str]) -> str:
    parts: list[str] = []
    for h in hashtags:
        tag = h.strip()
        if not tag:
            continue
        parts.append(tag if tag.startswith("#") else f"#{tag}")
    return " ".join(parts)


def _truncate(text: str, max_len: int, *, suffix: str = "…") -> str:
    if len(text) <= max_len:
        return text
    if max_len <= len(suffix):
        return text[:max_len]
    return text[: max_len - len(suffix)] + suffix


def build_instagram_caption(ctx: PublishCopyContext) -> str:
    """Story caption (fallback: short description) + blank line + hashtags; append source URL if room."""
    body = (ctx.story_caption or ctx.short_description or ctx.headline or "").strip()
    tags = _hashtag_line(ctx.hashtags)
    parts: list[str] = []
    if body:
        parts.append(body)
    if tags:
        parts.append(tags)
    caption = "\n\n".join(parts)

    source = (ctx.source_url or "").strip()
    if source:
        candidate = f"{caption}\n\n{source}" if caption else source
        if len(candidate) <= INSTAGRAM_MAX:
            caption = candidate
        elif len(caption) + len(source) + 2 <= INSTAGRAM_MAX:
            caption = f"{caption}\n\n{source}"

    return _truncate(caption, INSTAGRAM_MAX)


def build_x_text(ctx: PublishCopyContext) -> str:
    """Headline + short desc; hashtags inline; respect 280 chars."""
    parts: list[str] = []
    headline = (ctx.headline or "").strip()
    if headline:
        parts.append(headline)
    short = (ctx.short_description or "").strip()
    if short:
        parts.append(short)
    tags = _hashtag_line(ctx.hashtags)
    if tags:
        parts.append(tags)
    source = (ctx.source_url or "").strip()
    if source:
        parts.append(source)
    text = "\n\n".join(parts)
    return _truncate(text, X_MAX)


def build_linkedin_text(ctx: PublishCopyContext) -> str:
    """Story + hashtags; article link handled separately by LinkedIn share API."""
    parts: list[str] = []
    story = (ctx.story_caption or ctx.short_description or ctx.headline or "").strip()
    if story:
        parts.append(story)
    tags = _hashtag_line(ctx.hashtags)
    if tags:
        parts.append(tags)
    return "\n\n".join(parts)


def build_pinterest_description(ctx: PublishCopyContext) -> str:
    """Description = story + hashtags."""
    parts: list[str] = []
    story = (ctx.story_caption or ctx.short_description or "").strip()
    if story:
        parts.append(story)
    tags = _hashtag_line(ctx.hashtags)
    if tags:
        parts.append(tags)
    return "\n\n".join(parts)


def build_mastodon_text(ctx: PublishCopyContext) -> str:
    """Headline + story + hashtags + URL."""
    return _build_long_form_post(ctx)


def build_bluesky_text(ctx: PublishCopyContext) -> str:
    """Headline + story + hashtags + URL."""
    return _build_long_form_post(ctx)


def build_facebook_message(ctx: PublishCopyContext) -> str:
    """Message = headline + story (link passed separately)."""
    parts: list[str] = []
    headline = (ctx.headline or "").strip()
    if headline:
        parts.append(headline)
    story = (ctx.story_caption or ctx.short_description or "").strip()
    if story:
        parts.append(story)
    return "\n\n".join(parts)


def build_reddit_title(ctx: PublishCopyContext) -> str:
    return (ctx.headline or "").strip()


def build_devto_markdown(ctx: PublishCopyContext) -> str:
    return _build_article_markdown(ctx)


def build_hashnode_markdown(ctx: PublishCopyContext) -> str:
    return _build_article_markdown(ctx)


def build_blogger_html(ctx: PublishCopyContext) -> str:
    headline = (ctx.headline or "").strip()
    html_parts = [f"<h1>{headline}</h1>"] if headline else []
    body = (ctx.story_caption or ctx.short_description or "").strip()
    if body:
        for paragraph in body.split("\n\n"):
            p = paragraph.strip()
            if p:
                html_parts.append(f"<p>{p}</p>")
    tags = _hashtag_line(ctx.hashtags)
    if tags:
        html_parts.append(f"<p>{tags}</p>")
    source = (ctx.source_url or "").strip()
    if source:
        html_parts.append(f'<p><a href="{source}">Read more</a></p>')
    return "\n".join(html_parts)


def build_platform_copy(platform: str, ctx: PublishCopyContext) -> dict[str, str | None]:
    """Return platform-specific copy fields used by publishers."""
    platform = platform.lower()
    if platform == "instagram":
        return {"caption": build_instagram_caption(ctx), "title": ctx.headline}
    if platform == "x":
        return {"text": build_x_text(ctx)}
    if platform == "linkedin":
        return {"text": build_linkedin_text(ctx)}
    if platform == "pinterest":
        return {"title": ctx.headline, "description": build_pinterest_description(ctx)}
    if platform in ("mastodon", "bluesky"):
        return {"text": _build_long_form_post(ctx)}
    if platform == "facebook":
        return {"message": build_facebook_message(ctx)}
    if platform == "reddit":
        return {"title": build_reddit_title(ctx)}
    if platform == "devto":
        return {"title": ctx.headline, "body_markdown": build_devto_markdown(ctx)}
    if platform == "hashnode":
        return {"title": ctx.headline, "content_markdown": build_hashnode_markdown(ctx)}
    if platform == "blogger":
        return {"title": ctx.headline, "content_html": build_blogger_html(ctx)}
    # fallback: combined long form
    return {"text": _build_long_form_post(ctx)}


def publish_context_from_history(history) -> PublishCopyContext:
    hashtags = history.hashtags if isinstance(history.hashtags, list) else []
    source_url = None
    news = history.news_results
    if isinstance(news, list) and news:
        first = news[0]
        if isinstance(first, dict):
            source_url = first.get("link")
    return PublishCopyContext(
        headline=history.headline or "",
        short_description=history.description,
        story_caption=history.story_description,
        hashtags=[str(h) for h in hashtags],
        source_url=source_url,
    )


def _build_long_form_post(ctx: PublishCopyContext) -> str:
    parts: list[str] = []
    headline = (ctx.headline or "").strip()
    if headline:
        parts.append(headline)
    story = (ctx.story_caption or ctx.short_description or "").strip()
    if story:
        parts.append(story)
    tags = _hashtag_line(ctx.hashtags)
    if tags:
        parts.append(tags)
    source = (ctx.source_url or "").strip()
    if source:
        parts.append(source)
    return "\n\n".join(parts)


def _build_article_markdown(ctx: PublishCopyContext) -> str:
    parts = [f"# {(ctx.headline or '').strip()}"]
    body = (ctx.story_caption or ctx.short_description or "").strip()
    if body:
        parts.append(body)
    tags = _hashtag_line(ctx.hashtags)
    if tags:
        parts.append(tags)
    source = (ctx.source_url or "").strip()
    if source:
        parts.append(f"[Read more]({source})")
    return "\n\n".join(p for p in parts if p)
