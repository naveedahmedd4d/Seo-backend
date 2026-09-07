"""Parse WordPress post HTML into structured audit metrics."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import unquote, urlparse

try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

from app.services.blog_content_audit_config import ArticleType, WORD_TARGETS
from app.services.keyword_coverage_service import slug_from_url


@dataclass
class ParsedPost:
    title: str
    slug: str
    url: str
    article_type: str
    word_count: int
    word_target: int
    headings: list[dict[str, str]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    internal_links: int = 0
    external_links: int = 0
    images_total: int = 0
    images_missing_alt: int = 0
    meta_description: Optional[str] = None
    plain_text: str = ""
    content_hash: str = ""
    has_author_byline: bool = False
    has_last_updated_footer: bool = False
    paragraph_count: int = 0
    duplicate_paragraph_ratio: float = 0.0


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_body(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def content_hash_from_text(text: str) -> str:
    normalized = _normalize_body(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def guess_article_type(title: str, slug: str, url: str) -> str:
    t = (title or "").lower()
    s = (slug or "").lower()
    path = (urlparse(url).path or "").lower()

    if "net-worth" in s or "net worth" in t or "net-worth" in path:
        return "net_worth"
    if any(k in s for k in ("biography", "bio-", "-bio", "wiki")) or "biography" in t:
        return "biography"
    if "cluster-hub" in s or "cluster_hub" in s or ("guide" in s and "complete" in t):
        return "cluster_hub"
    if any(k in path for k in ("/news/", "/category/news")) or "news" in s:
        return "news"
    return "unknown"


def _extract_headings(soup: Any) -> list[dict[str, str]]:
    headings: list[dict[str, str]] = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(separator=" ", strip=True)
        if text:
            headings.append({"level": tag.name.lower(), "text": text})
    return headings


def _extract_sections(soup: Any) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_h2: Optional[str] = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, current_h2
        if current_h2 or buffer:
            text = " ".join(buffer).strip()
            sections.append(
                {
                    "heading": current_h2 or "(intro)",
                    "word_count": len(text.split()) if text else 0,
                    "text_preview": text[:400],
                }
            )
        buffer = []

    for el in soup.find_all(["h2", "h3", "p", "li"]):
        name = el.name.lower()
        text = el.get_text(separator=" ", strip=True)
        if not text:
            continue
        if name == "h2":
            flush()
            current_h2 = text
        elif name in ("p", "li", "h3"):
            buffer.append(text)
    flush()
    return sections


def _normalize_host(host: str) -> str:
    h = (host or "").lower()
    return h[4:] if h.startswith("www.") else h


def _link_counts(soup: Any, page_url: str) -> tuple[int, int]:
    internal = 0
    external = 0
    page_host = _normalize_host(urlparse(page_url).netloc)
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        if href.startswith(("http://", "https://")):
            host = _normalize_host(urlparse(href).netloc)
            if host and host == page_host:
                internal += 1
            elif host:
                external += 1
        elif href.startswith("/"):
            internal += 1
    return internal, external


def _image_stats(soup: Any) -> tuple[int, int]:
    total = 0
    missing_alt = 0
    for img in soup.find_all("img"):
        total += 1
        alt = (img.get("alt") or "").strip()
        if not alt:
            missing_alt += 1
    return total, missing_alt


def _paragraph_duplicate_ratio(paragraphs: list[str]) -> float:
    if len(paragraphs) < 2:
        return 0.0
    normalized = [_normalize_body(p) for p in paragraphs if len(p.split()) >= 8]
    if len(normalized) < 2:
        return 0.0
    unique = len(set(normalized))
    return round(1.0 - unique / len(normalized), 3)


def parse_post_content(
    *,
    html: str,
    title: str,
    url: str,
    meta_description: Optional[str] = None,
    slug_override: Optional[str] = None,
) -> ParsedPost:
    slug = slug_override or slug_from_url(url)
    slug = unquote(slug)
    article_type = guess_article_type(title, slug, url)
    word_target = WORD_TARGETS.get(article_type, WORD_TARGETS["unknown"])

    if not HAS_BS4:
        plain = _strip_tags(html)
        wc = len(plain.split()) if plain else 0
        return ParsedPost(
            title=title,
            slug=slug,
            url=url,
            article_type=article_type,
            word_count=wc,
            word_target=word_target,
            meta_description=meta_description,
            plain_text=plain,
            content_hash=content_hash_from_text(plain),
        )

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    plain = soup.get_text(separator=" ", strip=True)
    word_count = len(plain.split()) if plain else 0
    headings = _extract_headings(soup)
    sections = _extract_sections(soup)
    internal_links, external_links = _link_counts(soup, url)
    images_total, images_missing_alt = _image_stats(soup)

    paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if p]
    dup_ratio = _paragraph_duplicate_ratio(paragraphs)

    plain_lower = plain.lower()
    has_footer = "last updated" in plain_lower and "sources" in plain_lower
    has_byline = bool(soup.find(class_=re.compile(r"author|byline", re.I))) or "written by" in plain_lower

    return ParsedPost(
        title=title,
        slug=slug,
        url=url,
        article_type=article_type,
        word_count=word_count,
        word_target=word_target,
        headings=headings,
        sections=sections,
        internal_links=internal_links,
        external_links=external_links,
        images_total=images_total,
        images_missing_alt=images_missing_alt,
        meta_description=meta_description,
        plain_text=plain,
        content_hash=content_hash_from_text(plain),
        has_author_byline=has_byline,
        has_last_updated_footer=has_footer,
        paragraph_count=len(paragraphs),
        duplicate_paragraph_ratio=dup_ratio,
    )
