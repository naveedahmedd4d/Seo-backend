"""Keep Technology & AI research references out of reader-facing copy."""

import re
from html import unescape
from urllib.parse import urlsplit


def reader_content(text: str, site_url: str = "") -> str:
    """Remove external citation links; preserve images and local navigation.

    Research provenance is retained separately in workflow review data. This is
    a publication policy, not a general-purpose HTML security sanitizer.
    """
    if not text:
        return text
    site_host = urlsplit(site_url).hostname

    def internal(url: str) -> bool:
        url = unescape(url.strip())
        return (url.startswith(("/", "#")) and not url.startswith("//")) or bool(
            site_host and urlsplit(url).hostname == site_host
        )

    # A Sources/References section contains editorial provenance, not article copy.
    text = re.sub(
        r"^#{1,6}\s+(?:sources|references|further reading)\s*:?\s*\n.*?(?=^#{1,6}\s|\Z)",
        "", text, flags=re.I | re.M | re.S,
    )
    # Protect images, which are media assets rather than outbound navigation.
    images = []
    def protect(match):
        images.append(match.group(0))
        return f"\x00IMAGE{len(images) - 1}\x00"
    text = re.sub(r"!\[[^\]]*\]\([^\n]*?\)|<img\b[^>]*>", protect, text, flags=re.I)
    text = re.sub(
        r"(?<!!)\[[^\]]*\]\(\s*(<?(?:https?:)?//[^\s)]+>?)\s*(?:[\"'][^\n]*?[\"'])?\)",
        lambda m: m.group(0) if internal(m.group(1).strip("<>")) else "",
        text, flags=re.I,
    )
    text = re.sub(
        r"<a\b[^>]*\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))[^>]*>.*?</a\s*>",
        lambda m: m.group(0) if internal(next(v for v in m.groups() if v is not None)) else "",
        text, flags=re.I | re.S,
    )
    # Resolve external reference-style links before dropping their definitions.
    refs = dict(re.findall(r"^\s*\[([^\]]+)\]:\s*((?:https?:)?//\S+).*?$", text, re.M | re.I))
    for label, url in refs.items():
        if not internal(url):
            text = re.sub(r"(?<!!)\[[^\]]+\]\[" + re.escape(label) + r"\]", "", text, flags=re.I)
            text = re.sub(r"^\s*\[" + re.escape(label) + r"\]:[^\n]*", "", text, flags=re.M | re.I)
    text = re.sub(r"(?<!!)\[(?:S\d+|\d+(?:\s*[,;–-]\s*\d+)*|\^\d+)\](?![\[(])", "", text)
    text = re.sub(r"<?(?:https?:)?//[^\s<>\"')]+>?", lambda m: m.group(0) if internal(m.group(0).strip("<>")) else "", text)
    text = re.sub(r"([.!?])[ \t]+\.", r"\1", text)
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    for index, original in enumerate(images):
        text = text.replace(f"\x00IMAGE{index}\x00", original)
    return text.strip()


def clean_article_fields(article: dict, site_url: str = "") -> None:
    """Clean only public text, leaving private evidence and review data intact."""
    for field in ("body_content", "title", "meta_description"):
        if isinstance(article.get(field), str):
            article[field] = reader_content(article[field], site_url)
    for field, keys in (("faqs", ("question", "answer")), ("related_queries", ("query", "answer"))):
        for index, item in enumerate(article.get(field) or []):
            if isinstance(item, dict):
                for key in keys:
                    if isinstance(item.get(key), str):
                        item[key] = reader_content(item[key], site_url)
            elif isinstance(item, str):
                article[field][index] = reader_content(item, site_url)
