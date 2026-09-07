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
- Allowed uncertainty: "No public statement confirms X; however, Y is widely reported on Wikipedia."
- Every bullet in every section must be UNIQUE — never repeat the same idea or sentence across sections (no copy-paste bullets).

CELEBRITY-SPECIFIC SECTIONS:
- industry_context sections (Faith in the Limelight, Different Religions, Converting Faiths): max 2 short paragraphs; FIRST sentence names {name}; LAST sentence ties back to {name}; compare peers only briefly.
- Do not write generic Hollywood essays — every paragraph must mention {name} or their specific situation.

MEDIA & CITATIONS:
- Do NOT include markdown images unless Research context provides a real https URL for that image. Never use example.com, placeholder, or image-url.
- Do NOT add a Table of Contents.
- Do NOT embed external hyperlinks. When attribution is needed, mention Wikipedia in plain text only. Last Updated line: use "Sources: Wikipedia" (plain text, no links)."""


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


_ATHLETE_MARKERS = re.compile(
    r"\b(wnba|nba|nfl|mlb|nhl|soccer|football|basketball|tennis|olympics?|"
    r"athlete|player|coach|goalkeeper|quarterback|pitcher|guard|forward|"
    r"premier league|champions league|fifa|uefa)\b",
    re.IGNORECASE,
)


def is_athlete_biography_topic(
    topic: Optional[str] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    parent_category: Optional[str] = None,
) -> bool:
    """True when biography should use the athlete outline (not entertainment)."""
    cat = (category or "").strip()
    parent = (parent_category or "").strip()
    if cat == "Sports" or parent == "Sports":
        return True
    blob = " ".join(
        x for x in [(topic or ""), (description or ""), cat, parent] if x
    )
    if re.search(r"\bparent cluster category:\s*sports\b", blob, re.I):
        return True
    return bool(_ATHLETE_MARKERS.search(blob))


def get_athlete_biography_template() -> List[OutlineSection]:
    """Sports/athlete biography — no First Leading Role / entertainment H3s."""
    return [
        _h2(
            "intro",
            "Introduction",
            "intro",
            (180, 220),
            "Who {name} is, sport/league, why they matter today. No religion. No news-headline H1 inside body.",
        ),
        _h2(
            "quick_facts",
            "Quick Facts",
            "celebrity_focus",
            (80, 100),
            "Markdown table: Profession, Date of Birth, Nationality, Current team/league. Omit unknown rows. No Religion row.",
        ),
        _h2(
            "early_life",
            "Early Life & College",
            "celebrity_focus",
            (220, 280),
            "Birthplace, family sports background, high school, college program and accolades.",
        ),
        _h2(
            "first_media",
            "First Media Appearance / Interview Embed",
            "celebrity_focus",
            (100, 140),
            "YouTube interview embed or note; early media significance for {name}.",
        ),
        _h2(
            "pro_career",
            "Draft & Pro Career",
            "celebrity_focus",
            (400, 500),
            "Draft position and pro path for {name}.",
            [
                _h3(
                    "draft_debut",
                    "Draft & Debut",
                    "celebrity_focus",
                    (120, 150),
                    "Draft year/pick, first team, rookie impact.",
                ),
                _h3(
                    "teams_seasons",
                    "Teams & Standout Seasons",
                    "celebrity_focus",
                    (140, 180),
                    "Teams, trades, standout seasons with verified stats where research supports.",
                ),
                _h3(
                    "recent_form",
                    "Recent Form",
                    "celebrity_focus",
                    (120, 150),
                    "Current team role and recent notable games — no invented opponents.",
                ),
            ],
        ),
        _h2(
            "playing_style",
            "Playing Style & Notable Games",
            "celebrity_focus",
            (180, 220),
            "Style of play and 2–3 verified signature performances.",
        ),
        _h2(
            "honors",
            "Honors & Awards",
            "celebrity_focus",
            (160, 200),
            "All-Star, MVP, championships, Olympic medals — chronological, verified only.",
        ),
        _h2(
            "off_court",
            "Off the Court",
            "celebrity_focus",
            (140, 180),
            "Public philanthropy, business, NIL/endorsements if sourced. Omit if no facts.",
        ),
        _h2(
            "conclusion",
            "Conclusion",
            "conclusion",
            (120, 160),
            "Career summary and forward-looking close for {name}. No religion.",
        ),
    ]


def build_athlete_biography_outline(
    celebrity_name: Optional[str] = None, query: str = ""
) -> List[Dict[str, Any]]:
    name = celebrity_name or normalize_celebrity_name(query)
    return [_section_to_heading_nodes(s, name) for s in get_athlete_biography_template()]


def get_athlete_biography_section_slugs() -> List[str]:
    slugs: List[str] = []

    def walk(sections: List[OutlineSection]) -> None:
        for s in sections:
            slugs.append(s.slug)
            walk(s.children)

    walk(get_athlete_biography_template())
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


NET_WORTH_TARGET_WORD_COUNT = 1200

NET_WORTH_FORMAT_RULES = """LENGTH (required — do not stop early):
- Total article body: approximately 1,200 words (minimum 1,150, maximum 1,300).
- Write EVERY H2 and H3 in the outline. Stopping after the intro or Quick Facts is a failure.
- Each section MUST meet its word_budget. If dollar amounts are missing, expand with role context, income channels, career timeline, and how estimates work — never pad with placeholder phrases.
- Prioritize intro, quick_facts, net_worth_income, and methodology if trimming is needed.
- REQUIRED: include a Documented compensation subsection (year | team/league | salary or NIL) when research supports any pay figure — even if private net worth is unknown.
- If Investments or Personal Life have no research-backed specifics, OMIT that entire section — do not write three generic bullets about privacy, philanthropy, or lifestyle.

FORMATTING (required):
- Write prose in short paragraphs of 2–4 sentences each.
- After the opening prose for each H2 and each H3, include one markdown bullet list with 3–4 bullet points.
- Each bullet must be one clear, factual point with figures where research supports them.
- Introduction: if research has a credible compensation or net-worth figure, put it in the FIRST two sentences with as-of year and primary income sources. If there is no credible figure, open with who the person is and how they earn money — do not write "not publicly disclosed" and do not invent a private fortune.
- Quick Facts table columns: Fact | Details. Only include a row when you have a real value from research. Omit Date of Birth, Partner, Salary, or any other row if the value is unknown.
- BANNED table/cell text: "Not publicly disclosed", "Not publicly itemized", "N/A", "Unknown", "Undisclosed", "—". Never invent a value to fill a row.
- Do not add a Table of Contents.
- Do NOT write an FAQ section, FAQ headings, or Q/A pairs in the body. FAQs are generated separately."""

NET_WORTH_QUALITY_RULES = """NET WORTH FIRST (critical — reader must get compensation clarity immediately):
- Prefer documented salary, contract, or NIL figures over inventing a private net-worth fortune.
- The summary and Introduction MUST state one headline compensation figure (salary/contract/NIL) or a narrow reported range with an as-of year in the first two sentences when research supports it. If research has no credible figure, lead with {name}'s role and income sources — do not write "not publicly disclosed" and do not invent a number.
- Name the primary income sources in the opening (e.g. salary, media, speaking, investments).
- H1 MUST contain "{name} Net Worth" and the current year. Preferred pattern: "{name} Net Worth {year}: Salary, Endorsements & Earnings". NEVER use "How She/He Built Her/His $XM Fortune".

INCOME BREAKDOWN (core SEO section):
- Dedicated H2 "{name} Net Worth & Income Sources" with H3 subsections for career earnings, media/endorsements/appearances, and investments/real estate (omit Investments if no facts).
- Include specific figures where research supports them. If a dollar amount is unavailable, describe the income channel (salary band, book/speaking deals, disclosed property) instead of inventing amounts.
- Label estimates clearly (e.g. "reported", "estimated", "approx.") when not verified.

STRUCTURE & TONE:
- Career & Achievements BEFORE Personal Life — order is fixed.
- Career & Achievements: money-relevant timeline only (roles, titles, turning points that affect income) — not childhood biography.
- Personal Life: only when research has concrete public facts; otherwise omit the section.
- Include H2 "How {name}'s Net Worth Is Estimated" covering assets minus liabilities, public disclosures vs third-party estimates, and why ranges exist.
- BANNED: religion, faith, spirituality, church, prayer, or non-financial belief content.
- Do NOT embed external hyperlinks; mention Wikipedia in plain text when attribution is needed.
- Include disclaimer once: "Net worth estimates are based on publicly available information and may vary."
- Do NOT include FAQ headings or Q/A pairs in the body. FAQs are generated in a separate field."""


def get_net_worth_quality_rules(name: str = "the celebrity") -> str:
    return NET_WORTH_QUALITY_RULES.replace("{name}", name)


def get_net_worth_format_rules() -> str:
    return NET_WORTH_FORMAT_RULES


CELEBRITY_NET_WORTH_TEMPLATE_ID = "celebrity_net_worth_v2"


def get_celebrity_net_worth_template() -> List[OutlineSection]:
    """Fixed net worth outline — figure first, income breakdown, methodology, no in-body FAQ."""
    return [
        _h2(
            "intro",
            "Introduction",
            "intro",
            (110, 130),
            "FIRST two sentences: state {name}'s net worth figure (or range) with as-of year and primary income sources "
            "when research supports a figure; otherwise open with who {name} is and how they earn money "
            "(do not write 'not publicly disclosed'). Then one sentence on why readers search for this now. "
            "Include the disclaimer once.",
        ),
        _h2(
            "quick_facts",
            "Quick Facts / {name} at a Glance",
            "celebrity_focus",
            (70, 90),
            "Markdown table with columns Fact | Details. Include only rows with real research values "
            "(Full Name, Date of Birth, Nationality, Profession, Estimated Net Worth, Primary Income Sources, Partner). "
            "Omit any row you cannot fill. Never write Not publicly disclosed, N/A, or Unknown.",
        ),
        _h2(
            "net_worth_income",
            "{name} Net Worth & Income Sources",
            "celebrity_focus",
            (280, 320),
            "Core SEO section: how {name} earns money. Use research-backed figures when present; "
            "otherwise describe the income channel without inventing amounts.",
            [
                _h3(
                    "career_earnings",
                    "Career Earnings & Salary",
                    "celebrity_focus",
                    (90, 110),
                    "Salary, contracts, prize money, public-office pay bands — specific figures from research only.",
                ),
                _h3(
                    "media_appearances",
                    "Media, Endorsements & Public Appearances",
                    "celebrity_focus",
                    (90, 110),
                    "Books, speaking, brand deals, commentary, appearances — named deals with values only if sourced.",
                ),
                _h3(
                    "investments_assets",
                    "Investments, Real Estate & Other Assets",
                    "celebrity_focus",
                    (90, 110),
                    "Disclosed property, investments, and other assets; describe categories if dollar amounts are unknown.",
                ),
            ],
        ),
        _h2(
            "methodology",
            "How {name}'s Net Worth Is Estimated",
            "celebrity_focus",
            (160, 180),
            "Assets minus liabilities; public disclosures vs third-party estimates; why ranges exist. "
            "Wikipedia for biography, not as a wealth audit. Do not invent a new headline figure here.",
        ),
        _h2(
            "career_achievements",
            "Career & Achievements",
            "celebrity_focus",
            (180, 200),
            "Money-relevant timeline for {name}: roles, titles, turning points that affect income. "
            "Paragraph + 3–4 bullets as Year — Role. Not a childhood biography.",
        ),
        _h2(
            "personal_life",
            "Personal Life",
            "celebrity_focus",
            (150, 170),
            "Household, partner, lifestyle hooks for {name}. Do not invent a spouse net worth if it is not public.",
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
