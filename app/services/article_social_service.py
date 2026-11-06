"""Turn a published WordPress article into social-post source material.

The news flow asks SerpAPI what the world is saying about a query. This flow
is the opposite: the user already wrote the article, so the post must be about
*their* text, not someone else's coverage. That means no Google News call and
no SerpAPI key needed for the copy -- the article body is the prompt source.

Images are the one place we still reach out: WordPress posts do not always
carry featured media, so a missing image falls back to the article's own body
images and then to a keyword image search.
"""

from __future__ import annotations

import html as html_module
import re
from typing import Optional

# Enough article text for the model to find an angle, without spending the
# whole context window on one post.
DEFAULT_EXCERPT_CHARS = 4000

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_IMG_SRC_RE = re.compile(
    r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)


def html_to_text(html: str, limit: int = DEFAULT_EXCERPT_CHARS) -> str:
    """Flatten post HTML into plain prompt text.

    Script and style bodies are dropped outright -- their contents are code,
    not prose, and would otherwise survive tag stripping and mislead the model.
    """
    if not html:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip()
    return text


def first_body_image(html: str) -> Optional[str]:
    """First <img src> in the article body, or None."""
    if not html:
        return None
    match = _IMG_SRC_RE.search(html)
    return match.group(1) if match else None


def image_keyword(title: str, target_keyword: Optional[str]) -> str:
    """What to search Google Images for when the article has no image.

    The stored target keyword is what the article was written to rank for, so
    it describes the subject better than a headline written for clicks.
    """
    if target_keyword and target_keyword.strip():
        return target_keyword.strip()
    return (title or "").strip()


def build_prompt_source(
    title: str, content_html: str, limit: int = DEFAULT_EXCERPT_CHARS
) -> str:
    """The text handed to the model: the article's title plus its body."""
    body = html_to_text(content_html, limit=limit)
    title = (title or "").strip()
    if body:
        return f"{title}\n\n{body}".strip()
    return title


def as_news_result(
    title: str, content_html: str, url: Optional[str], site: Optional[str],
    limit: int = DEFAULT_EXCERPT_CHARS,
) -> dict:
    """Shape the article like a news result so the existing generators take it.

    The headline/description/hashtag writers all read title/source/snippet/date
    off dicts. Presenting the article in that shape reuses them unchanged, so
    an article post comes out in exactly the same format as a news post.
    """
    return {
        "title": (title or "").strip(),
        "link": url or "",
        "source": site or "",
        "snippet": html_to_text(content_html, limit=limit),
        "date": "",
    }
