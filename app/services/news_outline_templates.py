"""News blog outline templates — timeline, explainer, and opinion/reaction article structures."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app.services.celebrity_outline_templates import (
    OutlineSection,
    _append_section_briefs,
    _h2,
    _section_to_heading_nodes,
    outline_to_markdown,
)

NEWS_TIMELINE_TEMPLATE_ID = "news_timeline_v1"
NEWS_EXPLAINER_TEMPLATE_ID = "news_explainer_v1"
NEWS_OPINION_TEMPLATE_ID = "news_opinion_v1"

NEWS_TIMELINE_INTENT = "news_timeline"
NEWS_EXPLAINER_INTENT = "news_explainer"
NEWS_OPINION_INTENT = "news_opinion"

NEWS_TIMELINE_TARGET_WORD_COUNT = 2000
NEWS_EXPLAINER_TARGET_WORD_COUNT = 2000
NEWS_OPINION_TARGET_WORD_COUNT = 1200

# ---------------------------------------------------------------------------
# Timeline template
# ---------------------------------------------------------------------------

TIMELINE_FORMAT_RULES = """LENGTH (required — do not stop early):
- Total article body: approximately 2,000 words (minimum 1,800, maximum 2,500).
- Write EVERY H2 and every H3 under the Timeline section.
- The Timeline section must contain at least 4 H3 entries, one per distinct date or event phase.

FORMATTING (required):
- Short paragraphs of 2–4 sentences.
- Timeline H3 headings must follow the pattern: ### [Month Day, Year] — [Event label]
- Each timeline H3: 2–4 sentences describing exactly what happened that day or phase.
- Do not add a Table of Contents.
- Do NOT write an FAQ section or Q/A pairs in the body. FAQs are generated separately.
- Do NOT embed external hyperlinks. Cite news outlets and official sources in plain text only.
- Do NOT write Early Life, Career Overview, First Leading Role, or biography sections.
- Last Updated line must appear at the end of Current Status."""

TIMELINE_QUALITY_RULES = """NEWS FIRST (critical):
- The What Happened section MUST answer who, what, and when in the first 2 sentences.
- Do NOT open with biography content — the subject's background goes only in the Background section.
- Timeline H3s must be built from the news sources provided; do not invent dates or events.
- Named reactions in the Reactions section must be attributed to specific people or organisations from the sources.
- Current Status must reflect the most recent update from the news sources, not speculation.
- What's Next is a forward-looking close — no invented speculation, no unsourced predictions.
- BANNED without a named source: standalone sentences about what "may happen" or "could lead to".
- Cite news outlets by name in plain text (e.g. "according to The Washington Post") — never embed URLs."""


def get_news_timeline_format_rules() -> str:
    return TIMELINE_FORMAT_RULES


def get_news_timeline_quality_rules() -> str:
    return TIMELINE_QUALITY_RULES


def get_news_timeline_template(subject: str = "the subject") -> List[OutlineSection]:
    """Fixed timeline outline: event summary → background → dated H3s → context → reactions → status → next."""
    return [
        _h2(
            "what_happened",
            "What Happened",
            "intro",
            (130, 160),
            f"FIRST 2 sentences: who is {subject}, what event occurred, and when. "
            "State the core fact directly — no biography opening, no vague hook. "
            "Third sentence: why this story is significant right now.",
        ),
        _h2(
            "background",
            f"Who Is {subject}?",
            "celebrity_focus",
            (100, 130),
            f"3–5 sentences only: {subject}'s role, title, or position and why they are a public figure. "
            "Do NOT write a full biography — no childhood, no career arc. "
            "One sentence connecting their role to the current event.",
        ),
        _h2(
            "timeline",
            "Timeline of Events",
            "celebrity_focus",
            (300, 500),
            "One H3 per distinct date or event phase from the news sources. "
            "Minimum 4 H3 entries. Format: ### [Month Day, Year] — [Event label]. "
            "Each entry: 2–4 sentences describing what happened, named participants, and direct outcome. "
            "Use only events that appear in the provided news sources.",
        ),
        _h2(
            "why_it_matters",
            "Why It Matters",
            "industry_context",
            (150, 180),
            "Political, legal, social, or cultural significance of the event. "
            f"Name {subject} in the first sentence. "
            "One paragraph: structural reason this story has broad impact beyond the news cycle. "
            "One paragraph: precedent or implications for related institutions or figures.",
        ),
        _h2(
            "reactions",
            "Reactions & Responses",
            "celebrity_focus",
            (150, 180),
            "Named reactions from officials, parties, or public figures drawn from news sources. "
            "One bullet point per named source — name the person/organisation, quote or paraphrase their statement. "
            "Do not attribute reactions to unnamed 'critics' or 'supporters' without a name.",
        ),
        _h2(
            "current_status",
            "Current Status",
            "celebrity_focus",
            (100, 130),
            "What is the situation as of the most recent news source? "
            "State the outcome, ongoing investigation, policy change, or latest development. "
            f"End with: > **Last Updated:** {date.today().strftime('%B %d, %Y')} | "
            "**Sources:** [outlet names from news bundle]",
        ),
        _h2(
            "whats_next",
            "What's Next",
            "conclusion",
            (80, 100),
            "Forward-looking close: next expected development, hearing, decision, or vote if known from sources. "
            "If nothing is confirmed, state what observers are watching without predicting outcomes. "
            "No invented speculation. No FAQ.",
        ),
    ]


def build_news_timeline_outline(
    subject: str = "the subject",
) -> List[Dict[str, Any]]:
    return [_section_to_heading_nodes(s, subject) for s in get_news_timeline_template(subject)]


def get_news_timeline_section_slugs() -> List[str]:
    slugs: List[str] = []

    def walk(sections: List[OutlineSection]) -> None:
        for s in sections:
            slugs.append(s.slug)
            walk(s.children)

    walk(get_news_timeline_template())
    return slugs


def outline_to_news_timeline_prompt_block(
    subject: str = "the subject",
    include_briefs: bool = True,
) -> str:
    outline = build_news_timeline_outline(subject)
    parts = [
        "ARTICLE STRUCTURE (follow these exact H2/H3 headings):",
        outline_to_markdown(outline),
        "",
        get_news_timeline_quality_rules(),
        "",
        get_news_timeline_format_rules(),
    ]
    if include_briefs:
        parts.append("\nPer-section guidance:")
        for section in get_news_timeline_template(subject):
            _append_section_briefs(parts, section, subject)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Explainer template
# ---------------------------------------------------------------------------

EXPLAINER_FORMAT_RULES = """LENGTH (required — do not stop early):
- Total article body: approximately 2,000 words (minimum 1,800, maximum 2,500).
- Write EVERY H2 in the outline.

FORMATTING (required):
- Short paragraphs of 2–4 sentences.
- Key Terms section must be a markdown table with columns: Term | Plain-English Definition. Minimum 4 rows.
- Real Examples section must name specific people, teams, contracts, or events — no hypothetical examples.
- Do not add a Table of Contents.
- Do NOT write an FAQ section or Q/A pairs in the body. FAQs are generated separately.
- Do NOT embed external hyperlinks. Cite sources in plain text only.
- Do NOT write a biography of any individual — this is a topical guide, not a profile."""

EXPLAINER_QUALITY_RULES = """ANSWER FIRST (critical):
- The Introduction MUST directly answer the core question in the first 2 sentences.
- The reader must understand what the topic is and why it is being discussed right now before reading anything else.
- Key Terms must define terms that appear in the article body — do not include terms not used.
- Real Examples must be sourced from the research or news context provided; do not invent statistics.
- Perspectives section must name specific people, organisations, or sides — no "some say / others say" without attribution.
- BANNED: opinion presented as fact; invented contract values; speculative legal conclusions.
- Cite named sources in plain text (e.g. "according to ESPN") — never embed URLs."""


def get_news_explainer_format_rules() -> str:
    return EXPLAINER_FORMAT_RULES


def get_news_explainer_quality_rules() -> str:
    return EXPLAINER_QUALITY_RULES


def get_news_explainer_template(topic: str = "the topic") -> List[OutlineSection]:
    """Fixed explainer outline: direct answer → background → key terms → deep dive → examples → perspectives."""
    year = date.today().year
    return [
        _h2(
            "intro",
            "Introduction",
            "intro",
            (120, 150),
            f"FIRST 2 sentences: directly answer what {topic} is and why people are searching it in {year}. "
            "Third sentence: who this guide is for. "
            "No vague 'in the world of...' opener — answer the question immediately.",
        ),
        _h2(
            "background",
            "How It Works",
            "celebrity_focus",
            (200, 250),
            f"Plain-language explanation of how {topic} functions. "
            "Assume a reader with zero prior knowledge. "
            "Use one analogy if it clarifies the mechanism. "
            "End with a bullet list of 3–4 key rules or principles.",
        ),
        _h2(
            "key_terms",
            "Key Terms",
            "celebrity_focus",
            (120, 150),
            "Markdown table — Term | Plain-English Definition. Minimum 4 rows, maximum 8 rows. "
            "Define only terms that appear in this article. "
            "Definitions must be one sentence each — plain English, no jargon in the definition itself.",
        ),
        _h2(
            "deep_dive",
            "The Details",
            "celebrity_focus",
            (200, 280),
            f"Core mechanism, formula, or process for {topic} in full detail. "
            "Walk through how it is calculated, structured, or applied step by step. "
            "Use bullet points or a numbered list for sequential steps if applicable. "
            "Cite specific figures or rules from research context where available.",
        ),
        _h2(
            "real_examples",
            "Real-World Examples",
            "celebrity_focus",
            (150, 200),
            "2–3 named, sourced examples from the research or news context. "
            "Each example: one sentence naming the person/team/case, one sentence stating the relevant figure or outcome. "
            "Do not invent contract values or statistics — use only figures present in the provided context.",
        ),
        _h2(
            "perspectives",
            "Different Perspectives",
            "industry_context",
            (120, 150),
            "Named perspectives from at least 2 sides: proponents and critics, or competing interpretations. "
            "One paragraph per perspective, starting with the name/group holding that view. "
            "Balanced — do not favour one side. "
            f"> **Last Updated:** {date.today().strftime('%B %d, %Y')} | **Sources:** [outlet names from research]",
        ),
    ]


def build_news_explainer_outline(
    topic: str = "the topic",
) -> List[Dict[str, Any]]:
    return [_section_to_heading_nodes(s, topic) for s in get_news_explainer_template(topic)]


def get_news_explainer_section_slugs() -> List[str]:
    slugs: List[str] = []

    def walk(sections: List[OutlineSection]) -> None:
        for s in sections:
            slugs.append(s.slug)
            walk(s.children)

    walk(get_news_explainer_template())
    return slugs


def outline_to_news_explainer_prompt_block(
    topic: str = "the topic",
    include_briefs: bool = True,
) -> str:
    outline = build_news_explainer_outline(topic)
    parts = [
        "ARTICLE STRUCTURE (follow these exact H2/H3 headings):",
        outline_to_markdown(outline),
        "",
        get_news_explainer_quality_rules(),
        "",
        get_news_explainer_format_rules(),
    ]
    if include_briefs:
        parts.append("\nPer-section guidance:")
        for section in get_news_explainer_template(topic):
            _append_section_briefs(parts, section, topic)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Opinion / reaction template
# ---------------------------------------------------------------------------

OPINION_FORMAT_RULES = """LENGTH (required):
- Total article body: approximately 1,200 words (minimum 1,000, maximum 1,500).
- Write EVERY H2 in the outline.
- The opening hook paragraph has NO H2 heading — it is a standalone paragraph before the first H2.

FORMATTING (required):
- Short paragraphs of 2–4 sentences.
- Reactions section: one bullet point per named source with attribution.
- Our Take section MUST begin with a clearly visible label: **Editorial opinion:**
- Do not add a Table of Contents.
- Do NOT write an FAQ section or Q/A pairs in the body. FAQs are generated separately.
- Do NOT embed external hyperlinks. Cite news outlets in plain text only."""

OPINION_QUALITY_RULES = """NEWS FIRST (critical):
- The opening hook paragraph (no H2) must be drawn from the news hook / story description provided. 
  Use it verbatim or near-verbatim.
- What Happened must state only verified facts from the news sources — no opinion in this section.
- The Reaction section must attribute every reaction to a named person or organisation.
- Context section: 2–3 specific past events or statements that relate to the current story — no vague "history of controversy".
- Why It Won't Go Away: structural reason — audience size, political stakes, legal implications, media cycle — not generic.
- Our Take is clearly labeled as editorial opinion. It must be SHORT (60–80 words). 
  Do not present opinion as fact anywhere else in the article.
- BANNED in What Happened: "allegedly", "supposedly", "some say" without a named source.
- Cite news outlets by name in plain text — never embed URLs."""


def get_news_opinion_format_rules() -> str:
    return OPINION_FORMAT_RULES


def get_news_opinion_quality_rules() -> str:
    return OPINION_QUALITY_RULES


def get_news_opinion_template(subject: str = "the subject") -> List[OutlineSection]:
    """Fixed opinion/reaction outline: hook → what happened → reactions → context → why it persists → our take."""
    return [
        _h2(
            "what_happened",
            "What Happened",
            "celebrity_focus",
            (150, 180),
            f"Factual summary of what {subject} did or said. "
            "Use only facts from the news sources provided. "
            "State the specific action, statement, or event — no vague framing. "
            "No opinion, no adjectives that editorialise (e.g. 'shocking', 'outrageous').",
        ),
        _h2(
            "the_reaction",
            "The Reaction",
            "celebrity_focus",
            (200, 250),
            "Named reactions from opponents, supporters, and media outlets from the news sources. "
            "One bullet per named source: [Name/Organisation] — [paraphrase or short quote]. "
            "Minimum 4 bullets. Attribute every reaction — no unnamed 'critics' or 'allies'.",
        ),
        _h2(
            "context",
            "Context: The Bigger Picture",
            "industry_context",
            (150, 180),
            f"2–3 specific past events or statements involving {subject} that relate to this story. "
            "One short paragraph per item. "
            "Shows whether this is a pattern or an isolated incident. "
            "Source each item from the research context where possible.",
        ),
        _h2(
            "why_it_wont_go_away",
            "Why This Story Won't Go Away",
            "industry_context",
            (120, 150),
            "Structural reason this story has staying power: "
            "audience size, political stakes, legal implications, media cycle dynamics, or public accountability angle. "
            "One clear paragraph — no vague 'public interest' framing.",
        ),
        _h2(
            "our_take",
            "Our Take",
            "conclusion",
            (60, 80),
            "Short editorial paragraph CLEARLY labeled as opinion. "
            "MUST begin with: '**Editorial opinion:**' "
            "60–80 words only. State one clear editorial position. "
            "No invented facts. No unsourced claims. No FAQ.",
        ),
    ]


def build_news_opinion_outline(
    subject: str = "the subject",
) -> List[Dict[str, Any]]:
    return [_section_to_heading_nodes(s, subject) for s in get_news_opinion_template(subject)]


def get_news_opinion_section_slugs() -> List[str]:
    slugs: List[str] = []

    def walk(sections: List[OutlineSection]) -> None:
        for s in sections:
            slugs.append(s.slug)
            walk(s.children)

    walk(get_news_opinion_template())
    return slugs


def outline_to_news_opinion_prompt_block(
    subject: str = "the subject",
    include_briefs: bool = True,
) -> str:
    outline = build_news_opinion_outline(subject)
    parts = [
        "ARTICLE STRUCTURE (follow these exact H2/H3 headings):",
        outline_to_markdown(outline),
        "",
        get_news_opinion_quality_rules(),
        "",
        get_news_opinion_format_rules(),
    ]
    if include_briefs:
        parts.append("\nPer-section guidance:")
        for section in get_news_opinion_template(subject):
            _append_section_briefs(parts, section, subject)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Detector helpers
# ---------------------------------------------------------------------------

def is_news_timeline_request(content_intent: Optional[str]) -> bool:
    return (content_intent or "").strip() == NEWS_TIMELINE_INTENT


def is_news_explainer_request(content_intent: Optional[str]) -> bool:
    return (content_intent or "").strip() == NEWS_EXPLAINER_INTENT


def is_news_opinion_request(content_intent: Optional[str]) -> bool:
    return (content_intent or "").strip() == NEWS_OPINION_INTENT


NEWS_INTENTS = {NEWS_TIMELINE_INTENT, NEWS_EXPLAINER_INTENT, NEWS_OPINION_INTENT}


def is_any_news_request(content_intent: Optional[str]) -> bool:
    return (content_intent or "").strip() in NEWS_INTENTS
