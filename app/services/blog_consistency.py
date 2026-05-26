"""
E-E-A-T consistency: cross-reference numerical and factual claims across parent and children
to avoid contradictory data (e.g. net worth in body vs FAQs, children count).
"""
import re
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

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


def sanitize_filmography_table(body: str, topic: str) -> str:
    """
    Remove filmography table rows that are clearly not about the celebrity (e.g. wrong 'William' film).
    Only runs in the ## Filmography section. Removes rows where Title is just the topic and Notes
    suggest an unrelated film (no documentary/royal/interview).
    """
    if not body or not topic:
        return body
    topic_word = topic.strip().split()[0] if topic.strip() else ""
    if not topic_word:
        return body
    # Limit to Filmography section (from ## Filmography to next ## or end)
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


def sanitize_net_worth_body(body: str) -> str:
    """
    Post-process net worth markdown: fix awkward internal-link artifacts, broken image
    placeholders, and redundant phrasing. Complements stricter net worth LLM prompts.
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
    return s
