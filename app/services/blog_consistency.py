"""
E-E-A-T consistency: cross-reference numerical and factual claims across parent and children
to avoid contradictory data (e.g. net worth in body vs FAQs, children count).
"""
import re
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

CITATION_POLICY_PROMPT = """CITATION POLICY (required):
- Do NOT embed external hyperlinks in the article body (no markdown links to third-party http/https URLs).
- When attribution is needed, mention the source in plain text only (e.g. "according to Wikipedia" or "Wikipedia reports that…").
- Use Wikipedia as the only named external source when a citation is required.
- Do not cite Forbes, Celebrity Net Worth, or other outlets by brand name unless unavoidable; prefer Wikipedia for biographical and factual claims.
- The Last Updated / Sources footer must be plain text only — never markdown links in that line."""


def last_updated_sources_block(date_str: str) -> str:
    """Standard Last Updated footer with Wikipedia as the sole external source."""
    return f"> **Last Updated:** {date_str} | **Sources:** Wikipedia"


_SITE_CTA_PHRASE = "for more insights, explore our"


def has_site_cta(text: str) -> bool:
    """True when the standard internal-link CTA is already present."""
    return bool(text and _SITE_CTA_PHRASE in text.lower())


def _normalize_cta_href(href: str) -> str:
    h = (href or "/").strip() or "/"
    if h.startswith("http://") or h.startswith("https://") or h.startswith("/"):
        return h
    return f"/{h.lstrip('/')}"


def append_site_cta_markdown(body: str, href: str = "/", text: Optional[str] = None) -> str:
    """Append internal-link CTA using markdown (relative href survives body sanitization)."""
    if has_site_cta(body):
        return body or ""
    link_href = _normalize_cta_href(href)
    lead = (text or "For more insights, explore our").strip() or "For more insights, explore our"
    if "[" in lead:
        line = f"\n\n{lead}\n"
    else:
        line = f"\n\n{lead} [website]({link_href}).\n"
    return (body or "").rstrip() + line


def append_site_cta_html(content: str, href: str = "/", text: Optional[str] = None) -> str:
    """Append internal-link CTA as HTML (for WordPress HTML publish pipeline)."""
    if has_site_cta(content):
        return content or ""
    import html as html_module

    link_href = _normalize_cta_href(href)
    escaped_href = html_module.escape(link_href, quote=True)
    lead = (text or "For more insights, explore our").strip() or "For more insights, explore our"
    if "<a " in lead.lower():
        line = f"\n\n<p>{lead}</p>"
    else:
        escaped_lead = html_module.escape(lead)
        line = f"\n\n<p>{escaped_lead} <a href=\"{escaped_href}\">website</a>.</p>"
    return (content or "").rstrip() + line


_EXTERNAL_MD_LINK_PATTERN = re.compile(
    r"\[([^\]]+)\]\((https?://[^)]+)\)",
    re.IGNORECASE,
)


def strip_external_markdown_links(body: str) -> str:
    """Remove external markdown hyperlinks, keeping anchor text."""
    if not body or not body.strip():
        return body
    return _EXTERNAL_MD_LINK_PATTERN.sub(r"\1", body)


# Patterns for money (e.g. $140 million, $150M, 140 million dollars)
MONEY_PATTERN = re.compile(
    r"\$?\s*(\d+(?:\.\d+)?)\s*(?:million|million\s*dollars?|M(?:\s*USD)?|billion|B)\b",
    re.I,
)
# Standalone number that might be net worth in millions
NUMBERS_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:million|M)\b", re.I)
# Children count: "5 children", "three children", "1 daughter"
CHILDREN_PATTERN = re.compile(
    r"\b(?:(\d+)|(one|two|three|four|five|six|seven|eight|nine|ten))\s*(?:children|sons?|daughters?|kids)\b",
    re.I,
)
WORD_TO_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

# Quick Facts table: | Zodiac Sign | Gemini | or | Zodiac | Cancer |
ZODIAC_ROW_PATTERN = re.compile(
    r"\|\s*Zodiac\s*(?:Sign)?\s*\|\s*([^|\n]+)\s*\|",
    re.I,
)


def extract_zodiac_from_text(text: str) -> Optional[str]:
    """Extract zodiac value from Quick Facts table in body/FAQs text."""
    if not text:
        return None
    m = ZODIAC_ROW_PATTERN.search(text)
    if not m:
        return None
    val = (m.group(1) or "").strip()
    return val if val else None


def _parse_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def extract_money_values(text: str) -> List[str]:
    """Extract money amounts (e.g. '$140 million') as normalized strings for comparison."""
    if not text:
        return []
    seen = set()
    out = []
    for m in MONEY_PATTERN.finditer(text):
        val = m.group(0).strip()
        if val not in seen:
            seen.add(val)
            out.append(val)
    if not out:
        for m in NUMBERS_PATTERN.finditer(text):
            val = m.group(0).strip()
            if val not in seen:
                seen.add(val)
                out.append(val)
    return out


def extract_children_counts(text: str) -> List[int]:
    """Extract stated number of children (e.g. '5 children', 'one daughter')."""
    if not text:
        return []
    out = []
    for m in CHILDREN_PATTERN.finditer(text):
        g1, g2 = m.group(1), m.group(2)
        if g1:
            n = _parse_int(g1)
            if n is not None:
                out.append(n)
        elif g2:
            out.append(WORD_TO_NUM.get(g2.lower(), 0))
    return out


def _body_and_faqs_text(node: Dict[str, Any]) -> str:
    parts = [node.get("body_content") or "", node.get("body_content") or ""]
    for faq in node.get("faqs") or []:
        if isinstance(faq, dict):
            parts.append(str(faq.get("question", "")) + " " + str(faq.get("answer", "")))
        else:
            parts.append(str(faq))
    return " ".join(parts)


def validate_facts_across_articles(
    parent: Dict[str, Any],
    children: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Cross-reference numerical claims between parent and children (body + FAQs).
    Returns (warnings_by_key, list_of_messages) for logging/UI. Optionally returns
    canonical values to normalize (e.g. single net worth, single children count).
    """
    warnings: List[str] = []
    warnings_by_key: Dict[str, Any] = {}

    # Net worth: body vs FAQs (often in child_1 = net worth article)
    parent_text = _body_and_faqs_text(parent)
    parent_money = extract_money_values(parent_text)
    parent_children = extract_children_counts(parent_text)

    child_texts = [_body_and_faqs_text(c) for c in children]
    all_money = parent_money + [v for t in child_texts for v in extract_money_values(t)]
    all_children = parent_children + [n for t in child_texts for n in extract_children_counts(t)]

    # Normalize money to comparable form (e.g. "140" from "$140 million")
    def _norm_money(s: str) -> Optional[float]:
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if s.lower().find("billion") != -1 and m:
            return float(m.group(1)) * 1000
        return float(m.group(1)) if m else None

    money_values = []
    for s in all_money:
        v = _norm_money(s)
        if v is not None:
            money_values.append((v, s))

    if len(money_values) >= 2:
        uniq = set(v[0] for v in money_values)
        if len(uniq) > 1:
            msg = f"Net worth mismatch across articles/FAQs: {[v[1] for v in money_values]}"
            warnings.append(msg)
            warnings_by_key["net_worth_mismatch"] = [v[1] for v in money_values]

    if len(all_children) >= 2:
        uniq_c = set(all_children)
        if len(uniq_c) > 1:
            msg = f"Children count mismatch: body/FAQs state different numbers ({list(uniq_c)})"
            warnings.append(msg)
            warnings_by_key["children_count_mismatch"] = list(uniq_c)

    # Zodiac / Quick Facts: compare across parent and children
    parent_body = parent.get("body_content") or ""
    all_bodies = [parent_body] + [c.get("body_content") or "" for c in children]
    zodiacs = []
    for t in all_bodies:
        z = extract_zodiac_from_text(t)
        if z:
            zodiacs.append(z)
    if len(zodiacs) >= 2:
        uniq_z = set(z.strip() for z in zodiacs)
        if len(uniq_z) > 1:
            msg = f"Zodiac mismatch across articles: {list(uniq_z)}"
            warnings.append(msg)
            warnings_by_key["zodiac_mismatch"] = list(uniq_z)
            # Canonical: use most frequent, or first from parent
            from collections import Counter
            canonical_zodiac = Counter(zodiacs).most_common(1)[0][0].strip()
            if not canonical_zodiac and zodiacs:
                canonical_zodiac = zodiacs[0].strip()
            if canonical_zodiac:
                warnings_by_key["canonical_zodiac"] = canonical_zodiac
                logger.warning(
                    "Fact consistency: zodiac mismatch. Using canonical '%s' across articles.",
                    canonical_zodiac,
                )

    # Optional: return canonical net worth (most frequent or from net-worth article)
    if money_values and "net_worth_mismatch" in warnings_by_key:
        # Net worth is always the last child (religion + net worth) or sole child (net worth only)
        net_worth_child = children[-1] if children else {}
        nw_body = (net_worth_child.get("body_content") or "")
        nw_faqs = " ".join(
            str(f.get("answer", "")) for f in (net_worth_child.get("faqs") or []) if isinstance(f, dict)
        )
        nw_text = nw_body + " " + nw_faqs
        nw_vals = extract_money_values(nw_text)
        if nw_vals:
            canonical = nw_vals[0]
            warnings_by_key["canonical_net_worth"] = canonical
            logger.warning(
                "Fact consistency: net worth mismatch. Consider using canonical value '%s' in all FAQs/bodies.",
                canonical,
            )

    return warnings_by_key, warnings


def apply_canonical_net_worth(
    parent: Dict[str, Any],
    children: List[Dict[str, Any]],
    canonical_net_worth: str,
) -> None:
    """
    Replace stated net worth in FAQ answers with canonical value to fix mismatch.
    Modifies structures in place; use after validate_facts_across_articles.
    """
    def _replace_in_faqs(node: Dict[str, Any]) -> None:
        faqs = node.get("faqs")
        if not isinstance(faqs, list):
            return
        for faq in faqs:
            if not isinstance(faq, dict):
                continue
            ans = str(faq.get("answer", ""))
            if re.search(r"\d+\s*(?:million|M)\b", ans, re.I) and "net worth" in ans.lower():
                # Replace first money mention with canonical
                new_ans = re.sub(
                    r"\$?\s*\d+(?:\.\d+)?\s*(?:million|million\s*dollars?|M(?:\s*USD)?)\b",
                    canonical_net_worth if canonical_net_worth.startswith("$") else f"${canonical_net_worth.replace('$', '').strip()}",
                    ans,
                    count=1,
                    flags=re.I,
                )
                if not new_ans.startswith("$") and canonical_net_worth.startswith("$"):
                    new_ans = re.sub(
                        r"\$?\s*\d+(?:\.\d+)?\s*(?:million|M)\b",
                        canonical_net_worth,
                        ans,
                        count=1,
                        flags=re.I,
                    )
                faq["answer"] = new_ans

    _replace_in_faqs(parent)
    for c in children:
        _replace_in_faqs(c)


# For replacement: capture prefix (| Zodiac Sign |), value, suffix (|)
ZODIAC_ROW_REPLACE_PATTERN = re.compile(
    r"(\|\s*Zodiac\s*(?:Sign)?\s*\|\s*)([^|\n]+)(\s*\|)",
    re.I,
)


# Filmography table: rows like | 2019 | William | | A film about ... |
FILM_ROW_PATTERN = re.compile(
    r"^(\|\s*\d{4}\s*\|\s*)([^|]+)(\|\s*[^|]*\|\s*)([^|]*)(\s*\|)\s*$",
    re.MULTILINE,
)

_TOC_BLOCK_PATTERN = re.compile(
    r"\*\*Table of Contents\*\*\s*\n+(?:[-*]?\s*\[[^\]]+\]\([^)]+\)\s*\n+)+",
    re.IGNORECASE,
)
_TOC_H2_BLOCK_PATTERN = re.compile(
    r"^##\s+Table of Contents\s*\n+(?:[-*]?\s*\[[^\]]+\]\([^)]+\)\s*\n+)+",
    re.IGNORECASE | re.MULTILINE,
)


def strip_table_of_contents(body: str) -> str:
    """Remove markdown Table of Contents blocks from article body (Rank Math can generate its own)."""
    if not body or not body.strip():
        return body
    text = _TOC_BLOCK_PATTERN.sub("", body)
    text = _TOC_H2_BLOCK_PATTERN.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_COMPANION_CTA_PATTERN = re.compile(
    r"(?:For more information on|To learn more about|For details on|Read more about|Learn more about)"
    r"[^\n.]*?"
    r"(?:net\s*worth|religion|spiritual beliefs)"
    r"[^\n.]*?"
    r"(?:visit|see|read|check|at)\s*"
    r"(?:\[[^\]]*\]\([^)]+\)|/[a-z0-9-]+(?:-[a-z0-9]+)*)"
    r"[^\n.]*?\.?\s*",
    re.IGNORECASE,
)
_COMPANION_MD_LINK_PATTERN = re.compile(
    r"\[[^\]]*\]\(/(?:person/)?[a-z0-9-]+-(?:net-worth|religion)/?\)\s*",
    re.IGNORECASE,
)
_COMPANION_LINE_PATTERN = re.compile(
    r"^[^\n]*\b/\[?[a-z0-9-]+-(?:net-worth|religion)\]?[^\n]*\n?",
    re.IGNORECASE | re.MULTILINE,
)


def strip_companion_article_links(body: str) -> str:
    """Remove in-body CTAs and markdown links to net-worth / religion companion articles."""
    if not body or not body.strip():
        return body
    text = _COMPANION_CTA_PATTERN.sub("", body)
    text = _COMPANION_MD_LINK_PATTERN.sub("", text)
    text = _COMPANION_LINE_PATTERN.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sanitize_blog_body(body: str, *, category: Optional[str] = None) -> str:
    """Remove generated TOC/companion links and apply category publication policy."""
    text = strip_table_of_contents(body)
    text = strip_companion_article_links(text)
    if category == "Technology & AI":
        from app.services.technology_ai_publication import reader_content
        text = reader_content(text)
    else:
        text = strip_external_markdown_links(text)
    return text


def sanitize_filmography_table(body: str, topic: str) -> str:
    """
    Remove filmography table rows that are clearly not about the celebrity (e.g. wrong 'William' film).
    Only runs in the ## Professional Journey section (or legacy ## Filmography). Removes rows where Title is just the topic and Notes
    suggest an unrelated film (no documentary/royal/interview).
    """
    if not body or not topic:
        return body
    topic_word = topic.strip().split()[0] if topic.strip() else ""
    if not topic_word:
        return body
    # Limit to Professional Journey section (legacy Filmography heading supported)
    filmography_start = body.find("## Professional Journey")
    if filmography_start == -1:
        filmography_start = body.find("## Filmography")
    if filmography_start == -1:
        return body
    rest = body[filmography_start:]
    next_h2 = rest.find("\n## ", 3)
    section = rest[: next_h2] if next_h2 != -1 else rest
    keep_safe = ("documentary", "royal", "interview", "appearance", "prince", "king", "queen", "duke", "documentaries")

    def _should_remove_line(match: re.Match) -> bool:
        title_cell = (match.group(2) or "").strip()
        notes_cell = (match.group(4) or "").strip().lower()
        topic_words = topic.strip().split()
        if title_cell != topic.strip() and title_cell not in topic_words:
            return False
        if any(s in notes_cell for s in keep_safe):
            return False
        if "film" in notes_cell or "movie" in notes_cell:
            return True
        if len(notes_cell) > 30 and "about" in notes_cell:
            return True
        return False

    def _repl(match: re.Match):
        if _should_remove_line(match):
            return ""
        return match.group(0)

    new_section = FILM_ROW_PATTERN.sub(_repl, section)
    new_section = re.sub(r"\n{3,}", "\n\n", new_section)
    if new_section == section:
        return body
    return body[:filmography_start] + new_section + (rest[len(section) :] if next_h2 != -1 else "")


def apply_canonical_zodiac(
    parent: Dict[str, Any],
    children: List[Dict[str, Any]],
    canonical_zodiac: str,
) -> None:
    """
    Replace Zodiac Sign / Zodiac cell in Quick Facts tables with canonical value.
    Modifies body_content in place; use after validate_facts_across_articles.
    """
    canon = canonical_zodiac.strip()

    def _replace_in_body(node: Dict[str, Any]) -> None:
        body = node.get("body_content")
        if not body:
            return
        new_body = ZODIAC_ROW_REPLACE_PATTERN.sub(
            lambda m: m.group(1) + canon + m.group(3), body, count=1
        )
        if canon and new_body != body:
            node["body_content"] = new_body

    _replace_in_body(parent)
    for c in children:
        _replace_in_body(c)


_NET_WORTH_FAQ_HEADING = re.compile(
    r"^##[ \t]+(?:faq\b|frequently asked questions\b).*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_net_worth_body_faq(body: str) -> str:
    """Remove in-body FAQ H2 blocks (FAQs are stored separately)."""
    if not body or not body.strip():
        return body
    match = _NET_WORTH_FAQ_HEADING.search(body)
    if not match:
        return body
    start = match.start()
    rest = body[match.end() :]
    next_h2 = re.search(r"^##[ \t]+\S", rest, re.MULTILINE)
    end = match.end() + next_h2.start() if next_h2 else len(body)
    cleaned = (body[:start] + body[end:]).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned)


_NET_WORTH_PLACEHOLDER_ROW = re.compile(
    r"^\|([^|\n]+)\|[\s*]*("
    r"not publicly disclosed|not publicly itemized|undisclosed|"
    r"n/?a|n\.a\.|unknown|not available"
    r")[\s*.]*\|\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_net_worth_placeholder_rows(body: str) -> str:
    """Drop Quick Facts rows whose value is a placeholder instead of a real fact."""
    if not body or not body.strip():
        return body
    cleaned = _NET_WORTH_PLACEHOLDER_ROW.sub("", body)
    return re.sub(r"\n{3,}", "\n\n", cleaned)


_HTML_BLOCK_PATTERN = re.compile(
    r"<(?:img|iframe|script|style)\b[^>]*>.*?</(?:iframe|script|style)>|<(?:img)\b[^>]*/?>",
    re.IGNORECASE | re.DOTALL,
)
_CODE_FENCE_PATTERN = re.compile(r"```[\s\S]*?```")
_MD_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_FORTUNE_H1_PATTERN = re.compile(
    r"^(.+?)\s+Net Worth\s+(\d{4})\s*:\s*How (?:She|He|They) Built (?:Her|His|Their) .+$",
    re.IGNORECASE,
)
_GENERIC_FILLER_SECTION = re.compile(
    r"(^##[ \t]+(?:Investments?,?\s*Real Estate(?:\s*&\s*Other Assets)?|Personal Life)\s*$)"
    r"([\s\S]*?)(?=^##[ \t]+\S|\Z)",
    re.IGNORECASE | re.MULTILINE,
)
_GENERIC_FILLER_MARKERS = re.compile(
    r"not publicly (?:available|disclosed)|specific (?:details|figures) (?:are|were) not|"
    r"relatively private personal life|lifestyle reflects? (?:her|his|their) success|"
    r"philanthropic efforts|engages? with (?:her|his|their) fans on social media",
    re.IGNORECASE,
)


def count_markdown_words(body: str) -> int:
    """Count words in markdown body after stripping images, iframes, and code fences."""
    if not body or not body.strip():
        return 0
    text = _HTML_BLOCK_PATTERN.sub(" ", body)
    text = _CODE_FENCE_PATTERN.sub(" ", text)
    text = _MD_IMAGE_PATTERN.sub(" ", text)
    text = _MD_LINK_PATTERN.sub(r"\1", text)
    text = re.sub(r"[#>*_`~\-|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return 0
    return len(text.split(" "))


def is_thin_body(body: str, min_words: int) -> bool:
    return count_markdown_words(body) < max(0, int(min_words or 0))


def rewrite_fortune_h1(title: str, name: str = "", year: Optional[int] = None) -> str:
    """Rewrite 'How She Built Her $XM Fortune' titles to salary/earnings pattern."""
    from datetime import date

    t = (title or "").strip()
    if not t:
        return t
    m = _FORTUNE_H1_PATTERN.match(t)
    if not m:
        # Also catch titles that only end with Fortune without a clean prefix
        if re.search(r"\bHow (?:She|He|They) Built (?:Her|His|Their) .+ Fortune\b", t, re.I):
            display = (name or "").strip() or "Celebrity"
            y = year or date.today().year
            return f"{display} Net Worth {y}: Salary, Endorsements & Earnings"
        return t
    display = (name or m.group(1) or "").strip() or "Celebrity"
    y = year or int(m.group(2))
    return f"{display} Net Worth {y}: Salary, Endorsements & Earnings"


_MONEY_PHRASE = re.compile(
    r"(?:"
    r"\$[\d,.]+(?:\s*(?:million|billion|k|m|b))?"
    r"|"
    r"[\d,.]+\s*(?:million|billion)\s*(?:dollars?)?"
    r")",
    re.IGNORECASE,
)
_COMP_CONTEXT = re.compile(
    r"\b(?:salary|salaries|contract|nil|compensation|earned|earns|paycheck|base pay)\b",
    re.IGNORECASE,
)
_FORTUNE_CONTEXT = re.compile(r"\b(?:net worth|fortune|estimated worth)\b", re.IGNORECASE)


def extract_headline_compensation(body: str) -> Optional[str]:
    """First salary/NIL/contract money phrase from intro or Quick Facts; skip fortune guesses."""
    if not body or not body.strip():
        return None
    # Prefer Quick Facts table cells, then first ~800 chars of prose
    chunks: List[str] = []
    qf = re.search(
        r"^##[ \t]+Quick Facts\s*$([\s\S]*?)(?=^##[ \t]+\S|\Z)",
        body,
        re.IGNORECASE | re.MULTILINE,
    )
    if qf:
        chunks.append(qf.group(1))
    intro = body.split("\n## ", 1)[0]
    chunks.append(intro[:1200])
    blob = "\n".join(chunks)
    salary_hit: Optional[str] = None
    fortune_hit: Optional[str] = None
    for m in _MONEY_PHRASE.finditer(blob):
        start = max(0, m.start() - 80)
        end = min(len(blob), m.end() + 80)
        window = blob[start:end]
        phrase = m.group(0).strip()
        if _COMP_CONTEXT.search(window):
            salary_hit = phrase
            break
        if fortune_hit is None and _FORTUNE_CONTEXT.search(window):
            fortune_hit = phrase
    return salary_hit or fortune_hit


def strip_generic_filler_sections(body: str) -> str:
    """Omit Investments/Personal Life sections that are only generic filler."""
    if not body or not body.strip():
        return body

    def _repl(match: re.Match) -> str:
        heading, content = match.group(1), match.group(2) or ""
        prose = re.sub(r"^[-*]\s+.*$", "", content, flags=re.MULTILINE)
        prose = re.sub(r"\s+", " ", prose).strip()
        if len(prose.split()) < 40 and _GENERIC_FILLER_MARKERS.search(content):
            return ""
        return match.group(0)

    cleaned = _GENERIC_FILLER_SECTION.sub(_repl, body)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def sanitize_net_worth_body(body: str) -> str:
    """
    Post-process net worth markdown: fix awkward internal-link artifacts, broken image
    placeholders, redundant phrasing, and accidental in-body FAQ sections.
    """
    if not body or not body.strip():
        return body
    s = body
    # Internal-link injector can produce "assets (their net worth)" or duplicate "net worth (their net worth)"
    s = re.sub(
        r",\s*and\s+assets\s*\(\s*their net worth\s*\)",
        ", and assets",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"\bnet worth\s*\(\s*their net worth\s*\)",
        "net worth",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"\s*\(\s*their net worth\s*\)\s*(?=[,.;:!?\n]|$)",
        "",
        s,
        flags=re.I,
    )
    # Broken / placeholder images from prompts
    s = re.sub(r"!\[[^\]]*\]\(\s*image-url\s*\)", "", s, flags=re.I)
    s = re.sub(r"!\[[^\]]*\]\(\s*https?://image-url[^\)]*\)", "", s, flags=re.I)
    s = strip_net_worth_placeholder_rows(s)
    s = strip_net_worth_body_faq(s)
    s = strip_generic_filler_sections(s)
    # Rewrite fortune-style markdown H1 if the model put one in the body
    lines = s.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# ") and not line.startswith("## "):
            lines[i] = "# " + rewrite_fortune_h1(line[2:].strip())
            break
    return "\n".join(lines)
