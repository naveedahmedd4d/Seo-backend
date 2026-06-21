"""Celebrity religion article outline — single source of truth for headings and format rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

CELEBRITY_RELIGION_TEMPLATE_ID = "celebrity_religion_v2"
CELEBRITY_CATEGORY = "Celebrities & Public Figures"
RELIGION_TARGET_WORD_COUNT = 1500

SectionRole = Literal["intro", "celebrity_focus", "industry_context", "inspiration", "conclusion"]


RELIGION_BODY_FORMAT_RULES = """LENGTH (required):
- Total article body: approximately 1,500 words (minimum 1,400, maximum 1,600). Do not exceed 1,600 words.
- Distribute length using per-section word_budget in the outline; prioritize celebrity_focus sections (spotlight, controversies, work) if you must trim.

FORMATTING (required for every H2 and H3 subsection):
- Write prose in short paragraphs of exactly 3–4 sentences each (no paragraph longer than 4 sentences).
- After the opening prose for each H2 and each H3, include one markdown bullet list with exactly 3–4 bullet points.
- Each bullet must be one clear, factual or analytical point (not a full paragraph); cite or attribute when using research.
- Pattern per subsection: 1 short intro paragraph (3–4 sentences) → bullet list (3–4 items) → optional 1 closing paragraph (3–4 sentences) if needed for transitions.
- For industry_context H3 subsections under tight word budget: 2 sentences + 3 bullets only (no closing paragraph).
- Do not use walls of text, numbered lists longer than 4 items, or sections with only bullets and no prose.
- Introduction: Quick Facts table allowed after first paragraph; include 3–4 preview bullets after the opening paragraph.
- Conclusion: 1 paragraph (3–4 sentences) + 3–4 takeaway bullets; no new unsupported facts in bullets."""

RELIGION_QUALITY_RULES = """ANSWER & FACTS (critical — reader must get a direct answer):
- In the Introduction, the FIRST paragraph (before Quick Facts) must directly answer "What religion is {name}?" in 3–4 sentences. State what is known (e.g. upbringing, cultural faith) and what is unconfirmed — never leave the reader guessing.
- Immediately after the Introduction (or as its closing), include H2 "## 5 Key Facts About {name}'s Spiritual Beliefs" with exactly 5 numbered facts:
  1. **Fact 1:** ...
  2. **Fact 2:** ...
  (through Fact 5). Each fact is one specific, verifiable claim tied to {name} (cite source in prose or bullets where possible).
- If research lacks confirmation, say so plainly (e.g. "She has not publicly confirmed current practice") — do NOT fill gaps with vague "may/might/could" hedging.

RESEARCH & TONE:
- Use the Research context block as primary source. Prefer direct quotes, interview references, birthplace/upbringing facts, and named projects.
- BANNED without a named source in the same section: standalone sentences like "may be," "might," "potentially," "could be" about {name}'s beliefs.
- Allowed uncertainty: "No public statement confirms X; however, Y is widely reported in [Source](url)."
- Every bullet in every section must be UNIQUE — never repeat the same idea or sentence across sections (no copy-paste bullets).

CELEBRITY-SPECIFIC SECTIONS:
- industry_context sections (Faith in the Limelight, Different Religions, Converting Faiths): max 2 short paragraphs; FIRST sentence names {name}; LAST sentence ties back to {name}; compare peers only briefly.
- Do not write generic Hollywood essays — every paragraph must mention {name} or their specific situation.

MEDIA & CITATIONS:
- Do NOT include markdown images unless Research context provides a real https URL for that image. Never use example.com, placeholder, or image-url.
- Do NOT add a Table of Contents.
- Cite sources as markdown links [Outlet](https://real-url) from research. Last Updated line: list ONLY outlets you actually linked in the article — never generic "Celebrity Net Worth, Forbes" without links."""


def get_religion_quality_rules(name: str = "the celebrity") -> str:
    return RELIGION_QUALITY_RULES.replace("{name}", name)


@dataclass
class OutlineSection:
    slug: str
    level: Literal[2, 3]
    heading: str
    role: SectionRole
    word_budget: Tuple[int, int]
    llm_brief: str
    children: List["OutlineSection"] = field(default_factory=list)


def get_religion_format_rules() -> str:
    return RELIGION_BODY_FORMAT_RULES


def normalize_celebrity_name(query: str) -> str:
    """Derive display name from research/blog topic."""
    q = (query or "").strip()
    if not q:
        return "Celebrity"
    lower = q.lower()
    for prefix in (
        "what religion is ",
        "what is the religion of ",
        "religion of ",
        "religious beliefs of ",
    ):
        if lower.startswith(prefix):
            q = q[len(prefix) :].strip()
            lower = q.lower()
            break
    q = re.sub(r"\s+religion\s*$", "", q, flags=re.IGNORECASE).strip()
    for suffix in (" net worth", " salary", " earnings"):
        if lower.endswith(suffix):
            q = q[: -len(suffix)].strip()
            lower = q.lower()
            break
    if not q:
        return "Celebrity"
    return " ".join(part.capitalize() for part in q.split())


def _substitute_name(text: str, name: str) -> str:
    return (
        text.replace("[Celebrity Name]", name)
        .replace("{name}", name)
        .replace("{Name}", name)
    )


def _h3(slug: str, heading: str, role: SectionRole, budget: Tuple[int, int], brief: str) -> OutlineSection:
    return OutlineSection(
        slug=slug,
        level=3,
        heading=heading,
        role=role,
        word_budget=budget,
        llm_brief=brief,
    )


def _h2(
    slug: str,
    heading: str,
    role: SectionRole,
    budget: Tuple[int, int],
    brief: str,
    children: Optional[List[OutlineSection]] = None,
) -> OutlineSection:
    return OutlineSection(
        slug=slug,
        level=2,
        heading=heading,
        role=role,
        word_budget=budget,
        llm_brief=brief,
        children=children or [],
    )


def get_celebrity_religion_template() -> List[OutlineSection]:
    """Immutable religion outline template (headings before name substitution)."""
    return [
        _h2(
            "intro",
            "Introduction",
            "intro",
            (110, 130),
            "FIRST paragraph: direct answer to what religion {name} practices or was raised in (facts + gaps). "
            "Then Quick Facts table (Religion, Profession, DOB, Zodiac, Nationality); 3–4 unique preview bullets.",
        ),
        _h2(
            "five_key_facts",
            "5 Key Facts About [Celebrity Name]'s Spiritual Beliefs",
            "celebrity_focus",
            (130, 160),
            "Exactly 5 numbered facts (1.–5.), each one sentence, specific to {name}, sourced from research. "
            "Cover: upbringing faith, current practice (or lack of public confirmation), career/faith link, "
            "public vs private, one notable quote or interview if available.",
        ),
        _h2(
            "why_matters",
            "Faith in the Limelight: Why Celebrity Religion Matters",
            "industry_context",
            (90, 120),
            "Open naming {name}; why fans search for {name}'s faith; one sentence on media interest; tie back to {name} only.",
        ),
        _h2(
            "background",
            "Background & Overview",
            "celebrity_focus",
            (160, 190),
            "Family/upbringing context for {name}; childhood faith signals from research.",
            [
                _h3(
                    "background_hollywood",
                    "From Hollywood to the Holy — A Surprising Journey",
                    "celebrity_focus",
                    (80, 95),
                    "Path from fame to visible or private spirituality for {name}.",
                ),
                _h3(
                    "background_believe",
                    "Do Stars Really Believe? A Closer Look",
                    "celebrity_focus",
                    (80, 95),
                    "Evidence {name} holds sincere beliefs vs cultural identity only.",
                ),
            ],
        ),
        _h2(
            "spotlight",
            "[Celebrity Name]: The Faith Behind the Fame",
            "celebrity_focus",
            (230, 270),
            "Core religion profile for {name}; latest interviews/quotes; suggest [YOUTUBE_EMBED: interview] if applicable.",
            [
                _h3(
                    "spotlight_career",
                    "How [Celebrity Name]'s Religion Shapes Their Career & Choices",
                    "celebrity_focus",
                    (115, 135),
                    "Roles, statements, projects where faith influenced {name}'s decisions.",
                ),
                _h3(
                    "spotlight_identity",
                    "Faith, Identity, and Public Image",
                    "celebrity_focus",
                    (115, 135),
                    "How {name} presents belief publicly vs privately.",
                ),
            ],
        ),
        _h2(
            "hollywood_faiths",
            "Different Religions in Hollywood",
            "industry_context",
            (90, 120),
            "One paragraph: how {name}'s reported tradition compares to peers; name {name} in first and last sentence.",
            [
                _h3(
                    "hollywood_abrahamic",
                    "Christianity, Islam & Beyond: The Faiths of Top Stars",
                    "industry_context",
                    (45, 60),
                    "Only if relevant to {name}: their cultural/religious background vs other traditions.",
                ),
                _h3(
                    "hollywood_newage",
                    "Kabbalah, Scientology & New-Age Beliefs Among Celebrities",
                    "industry_context",
                    (45, 60),
                    "Mention {name} only if research links them to a movement; otherwise state not applicable.",
                ),
            ],
        ),
        _h2(
            "work",
            "How Religion Influences Their Work",
            "celebrity_focus",
            (160, 190),
            "Films, shows, music tied to {name}'s spiritual themes.",
            [
                _h3(
                    "work_screen",
                    "When Faith Meets the Big Screen",
                    "celebrity_focus",
                    (80, 95),
                    "Specific roles/projects for {name} with religious subtext.",
                ),
                _h3(
                    "work_art",
                    "Songs, Movies & Messages — Religion's Role in Celebrity Art",
                    "celebrity_focus",
                    (80, 95),
                    "Creative output reflecting {name}'s beliefs or questions.",
                ),
            ],
        ),
        _h2(
            "converting",
            "Converting Faiths",
            "industry_context",
            (90, 120),
            "State clearly whether {name} converted or kept childhood faith; one peer comparison max.",
            [
                _h3(
                    "converting_stars",
                    "Stars Who Changed Their Religion & Why",
                    "industry_context",
                    (45, 60),
                    "2–3 peer examples; compare to {name} if applicable.",
                ),
                _h3(
                    "converting_journey",
                    "From Doubt to Devotion: Celebrity Conversion Stories",
                    "industry_context",
                    (45, 60),
                    "Narrative pattern; slot {name} if research supports.",
                ),
            ],
        ),
        _h2(
            "public_private",
            "Public vs. Private Faith",
            "celebrity_focus",
            (140, 170),
            "How {name} handles prayer, church, social media faith signals.",
            [
                _h3(
                    "public_private_quiet",
                    "Praying Quietly or Preaching Loudly — Celebrity Choices",
                    "celebrity_focus",
                    (70, 85),
                    "{name}'s visible vs quiet practice.",
                ),
                _h3(
                    "public_private_secret",
                    "Why Some Stars Keep Their Faith a Secret",
                    "celebrity_focus",
                    (70, 85),
                    "Reasons {name} may limit faith talk (career, privacy, safety).",
                ),
            ],
        ),
        _h2(
            "controversies",
            "Controversies",
            "celebrity_focus",
            (140, 170),
            "Debates, skepticism, PR-faith accusations around {name}; cite latest sources.",
            [
                _h3(
                    "controversies_debate",
                    "When Celebrity Religion Sparks Debate",
                    "celebrity_focus",
                    (70, 85),
                    "Documented backlash or media cycles involving {name}.",
                ),
                _h3(
                    "controversies_pr",
                    "Faith or PR Move? Judging Celebrity Spirituality",
                    "celebrity_focus",
                    (70, 85),
                    "Balanced view on authenticity vs branding for {name}.",
                ),
            ],
        ),
        _h2(
            "inspiration",
            "What We Can Learn from Celebrities Who Live by Their Faith",
            "inspiration",
            (80, 100),
            "Lessons from {name}'s approach; universal takeaways; 80–100 words.",
        ),
        _h2(
            "conclusion",
            "Conclusion",
            "conclusion",
            (100, 120),
            "Closing section; subheading: Fame Fades, But Faith Remains — synthesize {name}'s spiritual story.",
            [
                _h3(
                    "conclusion_fame",
                    "Fame Fades, But Faith Remains",
                    "conclusion",
                    (50, 60),
                    "Forward-looking close on {name}'s faith journey.",
                ),
            ],
        ),
    ]


def _section_to_heading_nodes(section: OutlineSection, name: str) -> Dict[str, Any]:
    return {
        "level": section.level,
        "text": _substitute_name(section.heading, name),
        "children": [_section_to_heading_nodes(c, name) for c in section.children],
    }


def build_celebrity_religion_outline(celebrity_name: Optional[str] = None, query: str = "") -> List[Dict[str, Any]]:
    """Build HeadingNode-compatible outline dicts with name substitution."""
    name = celebrity_name or normalize_celebrity_name(query)
    return [_section_to_heading_nodes(s, name) for s in get_celebrity_religion_template()]


def get_religion_section_slugs() -> List[str]:
    slugs: List[str] = []

    def walk(sections: List[OutlineSection]) -> None:
        for s in sections:
            slugs.append(s.slug)
            walk(s.children)

    walk(get_celebrity_religion_template())
    return slugs


def outline_to_markdown(outline: List[Dict[str, Any]]) -> str:
    lines: List[str] = []

    def walk(nodes: List[Dict[str, Any]]) -> None:
        for n in nodes:
            level = n.get("level", 2)
            text = n.get("text", "")
            prefix = "##" if level == 2 else "###"
            lines.append(f"{prefix} {text}")
            walk(n.get("children") or [])

    walk(outline)
    return "\n".join(lines)


def outline_to_prompt_block(
    celebrity_name: str,
    include_briefs: bool = True,
) -> str:
    """Markdown structure + per-section briefs for blog religion prompt fallback."""
    outline = build_celebrity_religion_outline(celebrity_name)
    parts = [
        "ARTICLE STRUCTURE (follow these exact H2/H3 headings):",
        outline_to_markdown(outline),
        "",
        get_religion_quality_rules(celebrity_name),
        "",
        get_religion_format_rules(),
    ]
    if include_briefs:
        parts.append("\nPer-section guidance:")
        for section in get_celebrity_religion_template():
            _append_section_briefs(parts, section, celebrity_name)

    return "\n".join(parts)


def _append_section_briefs(parts: List[str], section: OutlineSection, name: str) -> None:
    budget = f"{section.word_budget[0]}-{section.word_budget[1]} words"
    parts.append(
        f"- [{section.slug}] {_substitute_name(section.heading, name)} ({section.role}, {budget}): "
        f"{_substitute_name(section.llm_brief, name)}"
    )
    for child in section.children:
        _append_section_briefs(parts, child, name)


CELEBRITY_BIOGRAPHY_TEMPLATE_ID = "celebrity_biography_v1"


def get_celebrity_biography_template() -> List[OutlineSection]:
    """Fixed biography outline aligned with parent overview blog structure."""
    return [
        _h2(
            "intro",
            "Introduction",
            "intro",
            (200, 250),
            "Who {name} is, why they are famous, what makes them relevant today. "
            "Do NOT repeat the news hook here if a separate news opening is provided.",
        ),
        _h2(
            "quick_facts",
            "Quick Facts",
            "celebrity_focus",
            (80, 100),
            "Table: Profession, Date of Birth, Zodiac Sign, Nationality (no religion column).",
        ),
        _h2(
            "early_life",
            "Early Life",
            "celebrity_focus",
            (250, 300),
            "Birthplace, family background, childhood interests; 3 bullet points on education and early inspirations.",
        ),
        _h2(
            "first_media",
            "First Media Appearance / Interview Embed",
            "celebrity_focus",
            (120, 150),
            "YouTube interview embed or note; significance of early media for {name}.",
        ),
        _h2(
            "career_overview",
            "Career Overview",
            "celebrity_focus",
            (400, 500),
            "Career arc for {name} with verified dates and milestones.",
            [
                _h3(
                    "career_beginnings",
                    "Career Beginnings",
                    "celebrity_focus",
                    (130, 160),
                    "How {name}'s career started; first opportunities and breakthroughs.",
                ),
                _h3(
                    "career_transition",
                    "Career Transition",
                    "celebrity_focus",
                    (130, 160),
                    "Shift from early work to mainstream success for {name}.",
                ),
                _h3(
                    "first_leading_role",
                    "First Leading Role",
                    "celebrity_focus",
                    (130, 160),
                    "First major leading role or equivalent turning point for {name}.",
                ),
            ],
        ),
        _h2(
            "personal_life",
            "Personal Life",
            "celebrity_focus",
            (250, 300),
            "Verified public information: family, marriage, children — neutral tone only.",
        ),
        _h2(
            "professional_journey",
            "Professional Journey",
            "celebrity_focus",
            (200, 250),
            "Career milestones for {name}: teams, roles, projects, media appearances — paragraph + bullet format (not a table).",
        ),
        _h2(
            "awards",
            "Awards & Nominations",
            "celebrity_focus",
            (200, 250),
            "Chronological awards and nominations for {name}; highlight major wins.",
        ),
        _h2(
            "public_image",
            "Public Image & Legacy",
            "celebrity_focus",
            (200, 300),
            "Influence, fan following, cultural impact, and how {name} is perceived today.",
        ),
        _h2(
            "conclusion",
            "Conclusion",
            "conclusion",
            (150, 200),
            "Summarize career journey, current projects, forward-looking close for {name}.",
        ),
    ]


def build_celebrity_biography_outline(
    celebrity_name: Optional[str] = None, query: str = ""
) -> List[Dict[str, Any]]:
    name = celebrity_name or normalize_celebrity_name(query)
    return [_section_to_heading_nodes(s, name) for s in get_celebrity_biography_template()]


def get_biography_section_slugs() -> List[str]:
    slugs: List[str] = []

    def walk(sections: List[OutlineSection]) -> None:
        for s in sections:
            slugs.append(s.slug)
            walk(s.children)

    walk(get_celebrity_biography_template())
    return slugs


def is_celebrity_religious_summary(category: Optional[str], celebrity_content_type: Optional[str]) -> bool:
    return (
        (category or "").strip() == CELEBRITY_CATEGORY
        and (celebrity_content_type or "").strip() == "religious"
    )


def is_celebrity_biography_summary(category: Optional[str], celebrity_content_type: Optional[str]) -> bool:
    return (
        (category or "").strip() == CELEBRITY_CATEGORY
        and (celebrity_content_type or "").strip() == "biography"
    )


NET_WORTH_TARGET_WORD_COUNT = 1500

NET_WORTH_FORMAT_RULES = """LENGTH (required):
- Total article body: approximately 1,500 words (minimum 1,400, maximum 1,600).
- Prioritize intro, quick_facts, and net_worth_income sections if trimming is needed.

FORMATTING (required):
- Write prose in short paragraphs of 2–4 sentences each.
- After the opening prose for each H2 and each H3, include one markdown bullet list with 3–4 bullet points.
- Each bullet must be one clear, factual point with figures where available.
- Introduction: net worth figure in the FIRST two sentences; Quick Facts table immediately after.
- Quick Facts table columns: Full Name | Data — rows for Full Name, Date of Birth, Nationality, Net Worth, Salary, Profession, Partner.
- Do not add a Table of Contents.
- FAQ section: 4–6 questions as H3 or bold Q/A pairs targeting People Also Ask queries."""

NET_WORTH_QUALITY_RULES = """NET WORTH FIRST (critical — reader must get the number immediately):
- The summary and Introduction MUST state one headline net worth figure (or a narrow range) with an as-of year in the first two sentences.
- Name the primary income sources in the opening (e.g. prize money, salary, endorsements).
- H1 MUST contain "{name} Net Worth" and the current year.

INCOME BREAKDOWN (core SEO section):
- Dedicated H2 "{name} Net Worth & Income Sources" with H3 subsections for career earnings, endorsements, and social media income.
- Include specific figures where research supports them (salary, prize money, brand deals, follower counts).
- Label estimates clearly (e.g. "reported", "estimated", "approx.") when not verified.

STRUCTURE & TONE:
- Career & Achievements BEFORE Personal Life — order is fixed.
- Early Life & Background: brief only (100–150 words max).
- Personal Life: high-traffic hook (partner, family); optional partner net worth comparison if relevant.
- BANNED: religion, faith, spirituality, church, prayer, or non-financial belief content.
- Cite sources as markdown links [Outlet](https://real-url) from research only.
- Include disclaimer once: "Net worth estimates are based on publicly available information and may vary."

FAQ:
- Target PAA-style queries: "{name} net worth", "how much does {name} make", "{name} salary", "{name} endorsements"."""


def get_net_worth_quality_rules(name: str = "the celebrity") -> str:
    return NET_WORTH_QUALITY_RULES.replace("{name}", name)


def get_net_worth_format_rules() -> str:
    return NET_WORTH_FORMAT_RULES


CELEBRITY_NET_WORTH_TEMPLATE_ID = "celebrity_net_worth_v1"


def get_celebrity_net_worth_template() -> List[OutlineSection]:
    """Fixed net worth outline — figure first, income breakdown core, career before personal life."""
    return [
        _h2(
            "intro",
            "Introduction",
            "intro",
            (120, 150),
            "FIRST two sentences: state {name}'s net worth figure (or range) with as-of year and primary income sources. "
            "Then one sentence on why readers search for this now.",
        ),
        _h2(
            "quick_facts",
            "Quick Facts / {name} at a Glance",
            "celebrity_focus",
            (80, 100),
            "Markdown table: Full Name, Date of Birth, Nationality, Net Worth, Salary, Profession, Partner — one row per fact.",
        ),
        _h2(
            "net_worth_income",
            "{name} Net Worth & Income Sources",
            "celebrity_focus",
            (400, 500),
            "Core SEO section: total wealth overview; how {name} earns money.",
            [
                _h3(
                    "career_earnings",
                    "Career Earnings & Salary",
                    "celebrity_focus",
                    (130, 160),
                    "Prize money, contracts, team salary, tournament payouts — specific figures from research.",
                ),
                _h3(
                    "endorsements",
                    "Brand Endorsements & Sponsorships",
                    "celebrity_focus",
                    (130, 160),
                    "Named brands and sponsorship deals with estimated values where available.",
                ),
                _h3(
                    "social_media",
                    "Social Media & Content Income",
                    "celebrity_focus",
                    (130, 160),
                    "Follower counts, per-post estimates, brand deal rates from research.",
                ),
            ],
        ),
        _h2(
            "career_achievements",
            "Career & Achievements",
            "celebrity_focus",
            (250, 300),
            "Major titles, awards, Olympic/medical milestones for {name}; paragraph + 3–4 bullets.",
        ),
        _h2(
            "early_life",
            "Early Life & Background",
            "celebrity_focus",
            (100, 150),
            "Brief: birthplace, upbringing, path into career — keep short; most readers skip this.",
        ),
        _h2(
            "personal_life",
            "Personal Life",
            "celebrity_focus",
            (150, 200),
            "Partner, family, high-traffic personal hooks; optional partner net worth comparison if relevant.",
        ),
        _h2(
            "faq",
            "FAQ: {name} Net Worth & More",
            "conclusion",
            (150, 200),
            "4–6 People Also Ask questions with concise answers; each answer restates or supports the headline net worth figure.",
        ),
    ]


def build_celebrity_net_worth_outline(
    celebrity_name: Optional[str] = None, query: str = ""
) -> List[Dict[str, Any]]:
    name = celebrity_name or normalize_celebrity_name(query)
    return [_section_to_heading_nodes(s, name) for s in get_celebrity_net_worth_template()]


def get_net_worth_section_slugs() -> List[str]:
    slugs: List[str] = []

    def walk(sections: List[OutlineSection]) -> None:
        for s in sections:
            slugs.append(s.slug)
            walk(s.children)

    walk(get_celebrity_net_worth_template())
    return slugs


def outline_to_net_worth_prompt_block(
    celebrity_name: str,
    include_briefs: bool = True,
) -> str:
    """Markdown structure + per-section briefs for net worth blog prompt fallback."""
    outline = build_celebrity_net_worth_outline(celebrity_name)
    parts = [
        "ARTICLE STRUCTURE (follow these exact H2/H3 headings):",
        outline_to_markdown(outline),
        "",
        get_net_worth_quality_rules(celebrity_name),
        "",
        get_net_worth_format_rules(),
    ]
    if include_briefs:
        parts.append("\nPer-section guidance:")
        for section in get_celebrity_net_worth_template():
            _append_section_briefs(parts, section, celebrity_name)

    return "\n".join(parts)


def is_celebrity_net_worth_summary(category: Optional[str], celebrity_content_type: Optional[str]) -> bool:
    return (
        (category or "").strip() == CELEBRITY_CATEGORY
        and (celebrity_content_type or "").strip() == "net_worth"
    )
