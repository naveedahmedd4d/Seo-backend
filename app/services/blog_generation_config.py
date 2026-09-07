"""Resolve per-user blog generation settings and outline templates."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.user_blog_settings import UserBlogSettings, UserBlogTemplate
from app.schemas.blog_settings import ARTICLE_TYPES, BlogGenerationConfig
from app.services.celebrity_outline_templates import (
    NET_WORTH_TARGET_WORD_COUNT,
    OutlineSection,
    RELIGION_TARGET_WORD_COUNT,
    get_athlete_biography_template,
    get_celebrity_biography_template,
    get_celebrity_net_worth_template,
    get_celebrity_religion_template,
    is_athlete_biography_topic,
    outline_to_markdown,
    _append_section_briefs,
    _substitute_name,
)
from app.services.cluster_hub_templates import (
    CLUSTER_HUB_TARGET_WORD_COUNT,
    get_cluster_hub_template,
    member_heading_slug,
    normalize_cluster_members,
)
from app.services.news_outline_templates import (
    NEWS_EXPLAINER_TARGET_WORD_COUNT,
    NEWS_OPINION_TARGET_WORD_COUNT,
    NEWS_TIMELINE_TARGET_WORD_COUNT,
    get_news_explainer_template,
    get_news_opinion_template,
    get_news_timeline_template,
)

DEFAULT_FAQ_COUNT = 5
DEFAULT_RELATED_COUNT = 8
DEFAULT_CTA_TEXT = "For more insights, explore our"
DEFAULT_CTA_HREF = "/"

CREATIVITY_TEMPERATURE = {
    "factual": 0.4,
    "balanced": 0.55,
    "expressive": 0.7,
}

SYSTEM_TARGETS: Dict[str, int] = {
    "biography": 2500,
    "athlete_biography": 1400,
    "religion": RELIGION_TARGET_WORD_COUNT,
    "net_worth": NET_WORTH_TARGET_WORD_COUNT,
    "cluster_hub": CLUSTER_HUB_TARGET_WORD_COUNT,
    "news_timeline": NEWS_TIMELINE_TARGET_WORD_COUNT,
    "news_explainer": NEWS_EXPLAINER_TARGET_WORD_COUNT,
    "news_opinion": NEWS_OPINION_TARGET_WORD_COUNT,
    "generic": 1000,
}

SYSTEM_NAMES: Dict[str, str] = {
    "biography": "Celebrity biography",
    "athlete_biography": "Athlete biography",
    "religion": "Religion / beliefs",
    "net_worth": "Net worth",
    "cluster_hub": "Cluster hub",
    "news_timeline": "News timeline",
    "news_explainer": "News explainer",
    "news_opinion": "Opinion / reaction",
    "generic": "Generic article",
}

_IMAGE_SLUGS: Dict[str, set] = {
    "biography": {"career_overview", "personal_life", "public_image"},
    "athlete_biography": {"pro_career", "off_court"},
    "religion": {"spotlight"},
    "net_worth": {"career_achievements", "personal_life"},
    "cluster_hub": set(),
    "news_timeline": {"timeline", "reactions"},
    "news_explainer": {"background", "real_examples"},
    "news_opinion": {"the_reaction", "context"},
    "generic": {"how_it_works"},
}

DEFAULT_FORMAT = {
    "paragraph_sentences": "2-4",
    "bullets_per_section": 4,
    "include_info_table": True,
}

_GENERIC_SECTIONS: List[Dict[str, Any]] = [
    {
        "slug": "intro",
        "level": 2,
        "heading": "Introduction",
        "role": "intro",
        "word_budget": [120, 180],
        "llm_brief": "Hook the reader and state what this article covers and who it is for.",
        "include_image": False,
        "children": [],
    },
    {
        "slug": "what_it_is",
        "level": 2,
        "heading": "What {topic} Is",
        "role": "celebrity_focus",
        "word_budget": [180, 240],
        "llm_brief": "Define the topic in plain language and why it matters now.",
        "include_image": False,
        "children": [],
    },
    {
        "slug": "how_it_works",
        "level": 2,
        "heading": "How It Works",
        "role": "celebrity_focus",
        "word_budget": [200, 280],
        "llm_brief": "Explain the mechanism, process, or key facts with a short bullet list.",
        "include_image": True,
        "children": [],
    },
    {
        "slug": "practical",
        "level": 2,
        "heading": "Practical Takeaways",
        "role": "industry_context",
        "word_budget": [160, 220],
        "llm_brief": "Actionable points, comparisons, or examples the reader can use.",
        "include_image": False,
        "children": [],
    },
    {
        "slug": "conclusion",
        "level": 2,
        "heading": "Conclusion",
        "role": "conclusion",
        "word_budget": [100, 140],
        "llm_brief": "Summarize key points and close with a forward-looking sentence.",
        "include_image": False,
        "children": [],
    },
]


def _section_to_dict(section: OutlineSection, image_slugs: set) -> Dict[str, Any]:
    children = [_section_to_dict(c, image_slugs) for c in section.children]
    kind = None
    if section.slug == "the_list":
        kind = "cluster_members"
        children = []
    return {
        "slug": section.slug,
        "level": int(section.level),
        "heading": section.heading,
        "role": section.role,
        "word_budget": [int(section.word_budget[0]), int(section.word_budget[1])],
        "llm_brief": section.llm_brief,
        "include_image": section.slug in image_slugs,
        "kind": kind,
        "children": children,
    }


def _placeholder_sections(article_type: str) -> List[OutlineSection]:
    if article_type == "biography":
        return get_celebrity_biography_template()
    if article_type == "athlete_biography":
        return get_athlete_biography_template()
    if article_type == "religion":
        return get_celebrity_religion_template()
    if article_type == "net_worth":
        return get_celebrity_net_worth_template()
    if article_type == "cluster_hub":
        return get_cluster_hub_template("topic", [])
    if article_type == "news_timeline":
        return get_news_timeline_template("the subject")
    if article_type == "news_explainer":
        return get_news_explainer_template("the topic")
    if article_type == "news_opinion":
        return get_news_opinion_template("the subject")
    return []


def get_system_template(article_type: str) -> Dict[str, Any]:
    if article_type not in ARTICLE_TYPES:
        raise ValueError(f"Unknown article_type: {article_type}")
    image_slugs = _IMAGE_SLUGS.get(article_type, set())
    if article_type == "generic":
        sections = copy.deepcopy(_GENERIC_SECTIONS)
    else:
        sections = [_section_to_dict(s, image_slugs) for s in _placeholder_sections(article_type)]
    return {
        "article_type": article_type,
        "name": SYSTEM_NAMES[article_type],
        "is_default": True,
        "is_system": True,
        "target_word_count": SYSTEM_TARGETS[article_type],
        "sections": sections,
        "format": dict(DEFAULT_FORMAT),
    }


def resolve_article_type(
    *,
    content_intent: Optional[str] = None,
    celebrity_content_type: Optional[str] = None,
    category: Optional[str] = None,
    topic: Optional[str] = None,
    description: Optional[str] = None,
    template_article_type: Optional[str] = None,
) -> str:
    if template_article_type and template_article_type in ARTICLE_TYPES:
        return template_article_type
    intent = (content_intent or "").strip()
    if intent in {"news_timeline", "news_explainer", "news_opinion", "cluster_hub", "religion", "net_worth"}:
        return intent
    if intent == "parent_overview" or (celebrity_content_type or "") == "biography":
        if is_athlete_biography_topic(topic, category, description):
            return "athlete_biography"
        return "biography"
    if (celebrity_content_type or "") == "religious":
        return "religion"
    if (celebrity_content_type or "") == "net_worth":
        return "net_worth"
    if (category or "").strip() == "Celebrities & Public Figures":
        if is_athlete_biography_topic(topic, category, description):
            return "athlete_biography"
        return "biography"
    return "generic"


def scale_section_budgets(
    sections: List[Dict[str, Any]],
    old_target: int,
    new_target: int,
) -> List[Dict[str, Any]]:
    if not old_target or old_target == new_target:
        return copy.deepcopy(sections)
    factor = max(0.4, min(2.5, new_target / float(old_target)))

    def walk(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for node in nodes:
            item = copy.deepcopy(node)
            budget = item.get("word_budget") or [100, 150]
            lo = max(30, min(2000, int(round(int(budget[0]) * factor))))
            hi = max(lo, min(2000, int(round(int(budget[1]) * factor))))
            item["word_budget"] = [lo, hi]
            item["children"] = walk(item.get("children") or [])
            out.append(item)
        return out

    return walk(sections)


def apply_intro_conclusion_length(sections: List[Dict[str, Any]], intro: str, conclusion: str) -> List[Dict[str, Any]]:
    factors = {"short": 0.75, "standard": 1.0, "long": 1.25}

    def scale_role(node: Dict[str, Any], role: str, length: str) -> None:
        if node.get("role") != role:
            return
        f = factors.get(length, 1.0)
        budget = node.get("word_budget") or [100, 150]
        lo = max(30, min(2000, int(round(int(budget[0]) * f))))
        hi = max(lo, min(2000, int(round(int(budget[1]) * f))))
        node["word_budget"] = [lo, hi]

    def walk(nodes: List[Dict[str, Any]]) -> None:
        for n in nodes:
            scale_role(n, "intro", intro)
            scale_role(n, "conclusion", conclusion)
            walk(n.get("children") or [])

    cloned = copy.deepcopy(sections)
    walk(cloned)
    return cloned


def expand_cluster_members(
    sections: List[Dict[str, Any]],
    members: Optional[Sequence[str]],
    topic: str,
) -> List[Dict[str, Any]]:
    names = normalize_cluster_members(members)
    cloned = copy.deepcopy(sections)

    def walk(nodes: List[Dict[str, Any]]) -> None:
        for n in nodes:
            if n.get("kind") == "cluster_members" or n.get("slug") == "the_list":
                n["kind"] = "cluster_members"
                n["children"] = [
                    {
                        "slug": member_heading_slug(name, i),
                        "level": 3,
                        "heading": name,
                        "role": "celebrity_focus",
                        "word_budget": [80, 120],
                        "llm_brief": (
                            f"80–120 words on {name}: role, why they are in this set, "
                            "and a research-backed figure only if available."
                        ),
                        "include_image": True,
                        "kind": None,
                        "children": [],
                    }
                    for i, name in enumerate(names)
                ]
            walk(n.get("children") or [])

    walk(cloned)
    return cloned


def dicts_to_heading_nodes(sections: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    def walk(node: Dict[str, Any]) -> Dict[str, Any]:
        heading = _substitute_name(str(node.get("heading") or ""), name)
        heading = heading.replace("{topic}", name).replace("{Topic}", name)
        return {
            "level": int(node.get("level") or 2),
            "text": heading,
            "children": [walk(c) for c in (node.get("children") or [])],
        }

    return [walk(s) for s in sections]


def sections_to_prompt_block(sections: List[Dict[str, Any]], name: str, include_briefs: bool = True) -> str:
    outline = dicts_to_heading_nodes(sections, name)
    parts = [
        "ARTICLE STRUCTURE (follow these exact H2/H3 headings):",
        outline_to_markdown(outline),
    ]
    if include_briefs:
        parts.append("\nPer-section guidance:")
        fake_sections = [_dict_to_outline_section(s) for s in sections]
        for section in fake_sections:
            _append_section_briefs(parts, section, name)
    return "\n".join(parts)


def _dict_to_outline_section(d: Dict[str, Any]) -> OutlineSection:
    budget = d.get("word_budget") or [100, 150]
    return OutlineSection(
        slug=str(d.get("slug") or "section"),
        level=2 if int(d.get("level") or 2) == 2 else 3,
        heading=str(d.get("heading") or "Section"),
        role=d.get("role") or "celebrity_focus",  # type: ignore[arg-type]
        word_budget=(int(budget[0]), int(budget[1])),
        llm_brief=str(d.get("llm_brief") or ""),
        children=[_dict_to_outline_section(c) for c in (d.get("children") or [])],
    )


def image_heading_slots(sections: List[Dict[str, Any]], name: str = "") -> List[Tuple[str, List[str]]]:
    slots: List[Tuple[str, List[str]]] = []

    def walk(nodes: List[Dict[str, Any]]) -> None:
        for n in nodes:
            if n.get("include_image") and n.get("kind") != "cluster_members":
                heading = _substitute_name(str(n.get("heading") or ""), name or "the subject")
                heading = heading.replace("{topic}", name or "").replace("{Topic}", name or "")
                slots.append((str(n.get("slug") or heading), [heading]))
            walk(n.get("children") or [])

    walk(sections)
    return slots


def min_words_for_target(target: int) -> int:
    return max(400, int(target * 0.85))


def max_tokens_for_target(target: int) -> int:
    return max(3500, min(8000, int(target) * 2))


def length_prompt_line(target: int) -> str:
    lo = min_words_for_target(target)
    hi = max(lo + 50, int(target * 1.25))
    return (
        f"LENGTH (required): Total article body approximately {target:,} words "
        f"(minimum {lo:,}, maximum {hi:,}). Write EVERY outline section."
    )


def format_rules_from_config(cfg: BlogGenerationConfig) -> str:
    fmt = cfg.format or DEFAULT_FORMAT
    para = fmt.get("paragraph_sentences") or "2-4"
    bullets = fmt.get("bullets_per_section")
    table = fmt.get("include_info_table", True)
    lines = [
        length_prompt_line(cfg.target_word_count),
        f"Write prose in short paragraphs of {para} sentences each.",
    ]
    if bullets:
        lines.append(
            f"After the opening prose for each H2, include one markdown bullet list with {bullets} points where it adds value."
        )
    if table:
        lines.append("Include a compact markdown information table where the outline asks for one.")
    else:
        lines.append("Do not include an information/facts table unless the outline explicitly requires it.")
    lines.append("Do not add a Table of Contents.")
    lines.append("Do NOT write an FAQ section or Q/A pairs in the body. FAQs are generated separately.")
    return "\n".join(f"- {line}" if not line.startswith("LENGTH") else line for line in lines)


def validate_template_sections(sections: List[Dict[str, Any]], article_type: str) -> None:
    if not sections:
        raise ValueError("Template must include at least one section")
    h2_count = sum(1 for s in sections if int(s.get("level") or 2) == 2)
    if h2_count < 1:
        raise ValueError("Template must include at least one H2")
    if h2_count > 20:
        raise ValueError("Template may have at most 20 H2 sections")

    slugs: List[str] = []

    def walk(nodes: List[Dict[str, Any]], parent_level: int) -> None:
        for n in nodes:
            slug = str(n.get("slug") or "").strip()
            if not slug:
                raise ValueError("Every section needs a slug")
            if slug in slugs:
                raise ValueError(f"Duplicate section slug: {slug}")
            slugs.append(slug)
            level = int(n.get("level") or 2)
            if level not in (2, 3):
                raise ValueError("Section level must be 2 or 3")
            if parent_level == 0 and level != 2:
                raise ValueError("Top-level sections must be H2")
            if parent_level == 2 and level != 3:
                raise ValueError("Children of H2 must be H3")
            if parent_level == 3:
                raise ValueError("H4 sections are not supported")
            heading = str(n.get("heading") or "").strip()
            if not heading:
                raise ValueError(f"Section {slug} needs a heading")
            walk(n.get("children") or [], level)

    walk(sections, 0)

    if article_type == "cluster_hub":
        kinds = []

        def collect(nodes: List[Dict[str, Any]]) -> None:
            for n in nodes:
                if n.get("kind") == "cluster_members" or n.get("slug") == "the_list":
                    kinds.append(n)
                collect(n.get("children") or [])

        collect(sections)
        if not kinds:
            raise ValueError("Cluster hub templates must keep a The List / cluster_members section")


def _row_to_settings_defaults(row: Optional[UserBlogSettings]) -> Dict[str, Any]:
    if row is None:
        return {
            "target_word_count": None,
            "intro_length": "standard",
            "conclusion_length": "standard",
            "faq_count": DEFAULT_FAQ_COUNT,
            "related_query_count": DEFAULT_RELATED_COUNT,
            "include_site_cta": True,
            "cta_text": DEFAULT_CTA_TEXT,
            "cta_href": DEFAULT_CTA_HREF,
            "auto_featured_image": True,
            "max_section_images": 3,
            "creativity": "balanced",
            "custom_style_notes": "",
            "seo_power_words": True,
            "last_updated_footer": True,
        }
    return {
        "target_word_count": row.target_word_count,
        "intro_length": row.intro_length or "standard",
        "conclusion_length": row.conclusion_length or "standard",
        "faq_count": int(row.faq_count if row.faq_count is not None else DEFAULT_FAQ_COUNT),
        "related_query_count": int(
            row.related_query_count if row.related_query_count is not None else DEFAULT_RELATED_COUNT
        ),
        "include_site_cta": bool(row.include_site_cta if row.include_site_cta is not None else True),
        "cta_text": (row.cta_text or DEFAULT_CTA_TEXT).strip() or DEFAULT_CTA_TEXT,
        "cta_href": (row.cta_href or DEFAULT_CTA_HREF).strip() or DEFAULT_CTA_HREF,
        "auto_featured_image": bool(row.auto_featured_image if row.auto_featured_image is not None else True),
        "max_section_images": int(row.max_section_images if row.max_section_images is not None else 3),
        "creativity": row.creativity or "balanced",
        "custom_style_notes": (row.custom_style_notes or "").strip(),
        "seo_power_words": bool(row.seo_power_words if row.seo_power_words is not None else True),
        "last_updated_footer": bool(row.last_updated_footer if row.last_updated_footer is not None else True),
    }


def load_template_payload(
    db: Optional[Session],
    user_id: Optional[int],
    article_type: str,
) -> Dict[str, Any]:
    system = get_system_template(article_type)
    if db is None or user_id is None:
        return system
    row = (
        db.query(UserBlogTemplate)
        .filter(UserBlogTemplate.user_id == user_id, UserBlogTemplate.article_type == article_type)
        .first()
    )
    if not row:
        return system
    return {
        "article_type": article_type,
        "name": row.name or system["name"],
        "is_default": bool(row.is_default),
        "is_system": False,
        "target_word_count": int(row.target_word_count or system["target_word_count"]),
        "sections": row.sections or system["sections"],
        "format": row.format_options or system["format"],
    }


def resolve_blog_generation_config(
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
    *,
    article_type: str,
    overrides: Optional[Dict[str, Any]] = None,
    cluster_subs: Optional[Sequence[str]] = None,
    topic: str = "",
) -> BlogGenerationConfig:
    overrides = overrides or {}
    settings_row = None
    if db is not None and user_id is not None:
        settings_row = db.query(UserBlogSettings).filter(UserBlogSettings.user_id == user_id).first()
    defaults = _row_to_settings_defaults(settings_row)
    tmpl = load_template_payload(db, user_id, article_type)

    target = overrides.get("target_word_count")
    if target is None:
        target = defaults["target_word_count"]
    if target is None:
        target = tmpl["target_word_count"]
    target = int(target)
    target = max(800, min(4000, target))

    sections = scale_section_budgets(tmpl["sections"], tmpl["target_word_count"], target)
    intro = overrides.get("intro_length") or defaults["intro_length"]
    conclusion = overrides.get("conclusion_length") or defaults["conclusion_length"]
    sections = apply_intro_conclusion_length(sections, intro, conclusion)
    if article_type == "cluster_hub":
        sections = expand_cluster_members(sections, cluster_subs, topic)

    creativity = overrides.get("creativity") or defaults["creativity"]
    faq_count = overrides.get("faq_count")
    if faq_count is None:
        faq_count = defaults["faq_count"]
    related_count = overrides.get("related_query_count")
    if related_count is None:
        related_count = defaults["related_query_count"]
    include_cta = overrides.get("include_site_cta")
    if include_cta is None:
        include_cta = defaults["include_site_cta"]
    auto_images = overrides.get("auto_images")
    if auto_images is None:
        auto_images = defaults["auto_featured_image"]

    return BlogGenerationConfig(
        article_type=article_type,
        target_word_count=target,
        min_words=min_words_for_target(target),
        max_tokens=max_tokens_for_target(target),
        temperature=CREATIVITY_TEMPERATURE.get(str(creativity), 0.55),
        faq_count=max(0, min(10, int(faq_count))),
        related_query_count=max(0, min(12, int(related_count))),
        include_site_cta=bool(include_cta),
        cta_text=str(overrides.get("cta_text") or defaults["cta_text"]),
        cta_href=str(overrides.get("cta_href") or defaults["cta_href"]),
        auto_featured_image=bool(auto_images),
        max_section_images=max(0, min(12, int(defaults["max_section_images"]))),
        creativity=str(creativity),
        custom_style_notes=str(overrides.get("custom_style_notes") or defaults["custom_style_notes"] or ""),
        seo_power_words=bool(
            defaults["seo_power_words"] if overrides.get("seo_power_words") is None else overrides.get("seo_power_words")
        ),
        last_updated_footer=bool(
            defaults["last_updated_footer"]
            if overrides.get("last_updated_footer") is None
            else overrides.get("last_updated_footer")
        ),
        intro_length=str(intro),
        conclusion_length=str(conclusion),
        sections=sections,
        format=tmpl.get("format") or dict(DEFAULT_FORMAT),
        template_name=str(tmpl.get("name") or ""),
    )


def get_or_create_blog_settings(db: Session, user_id: int) -> UserBlogSettings:
    row = db.query(UserBlogSettings).filter(UserBlogSettings.user_id == user_id).first()
    if row:
        return row
    row = UserBlogSettings(user_id=user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def settings_to_response(row: UserBlogSettings) -> Dict[str, Any]:
    return {
        "target_word_count": row.target_word_count,
        "intro_length": row.intro_length or "standard",
        "conclusion_length": row.conclusion_length or "standard",
        "faq_count": int(row.faq_count if row.faq_count is not None else DEFAULT_FAQ_COUNT),
        "related_query_count": int(
            row.related_query_count if row.related_query_count is not None else DEFAULT_RELATED_COUNT
        ),
        "include_site_cta": bool(row.include_site_cta if row.include_site_cta is not None else True),
        "cta_text": row.cta_text,
        "cta_href": row.cta_href,
        "auto_featured_image": bool(row.auto_featured_image if row.auto_featured_image is not None else True),
        "max_section_images": int(row.max_section_images if row.max_section_images is not None else 3),
        "creativity": row.creativity or "balanced",
        "custom_style_notes": row.custom_style_notes,
        "seo_power_words": bool(row.seo_power_words if row.seo_power_words is not None else True),
        "last_updated_footer": bool(row.last_updated_footer if row.last_updated_footer is not None else True),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def upsert_blog_settings(db: Session, user_id: int, **fields: Any) -> UserBlogSettings:
    row = get_or_create_blog_settings(db, user_id)
    allowed = {
        "target_word_count",
        "intro_length",
        "conclusion_length",
        "faq_count",
        "related_query_count",
        "include_site_cta",
        "cta_text",
        "cta_href",
        "auto_featured_image",
        "max_section_images",
        "creativity",
        "custom_style_notes",
        "seo_power_words",
        "last_updated_footer",
    }
    for key, value in fields.items():
        if key in allowed and value is not None:
            setattr(row, key, value)
    if "target_word_count" in fields and fields["target_word_count"] is None:
        row.target_word_count = None
    db.commit()
    db.refresh(row)
    return row


def upsert_user_template(
    db: Session,
    user_id: int,
    article_type: str,
    *,
    name: Optional[str],
    target_word_count: Optional[int],
    sections: List[Dict[str, Any]],
    format_dict: Optional[Dict[str, Any]],
) -> UserBlogTemplate:
    validate_template_sections(sections, article_type)
    system = get_system_template(article_type)
    row = (
        db.query(UserBlogTemplate)
        .filter(UserBlogTemplate.user_id == user_id, UserBlogTemplate.article_type == article_type)
        .first()
    )
    payload_sections = [s if isinstance(s, dict) else s for s in sections]
    if row is None:
        row = UserBlogTemplate(
            user_id=user_id,
            article_type=article_type,
            name=name or system["name"],
            is_default=True,
            target_word_count=int(target_word_count or system["target_word_count"]),
            sections=payload_sections,
            format_options=format_dict or system["format"],
        )
        db.add(row)
    else:
        if name:
            row.name = name
        if target_word_count:
            row.target_word_count = int(target_word_count)
        row.sections = payload_sections
        if format_dict is not None:
            row.format_options = format_dict
    db.commit()
    db.refresh(row)
    return row


def reset_user_template(db: Session, user_id: int, article_type: str) -> Dict[str, Any]:
    row = (
        db.query(UserBlogTemplate)
        .filter(UserBlogTemplate.user_id == user_id, UserBlogTemplate.article_type == article_type)
        .first()
    )
    if row:
        db.delete(row)
        db.commit()
    return get_system_template(article_type)


def list_user_templates(db: Session, user_id: int) -> List[Dict[str, Any]]:
    rows = {r.article_type: r for r in db.query(UserBlogTemplate).filter(UserBlogTemplate.user_id == user_id).all()}
    out: List[Dict[str, Any]] = []
    for article_type in ARTICLE_TYPES:
        if article_type in rows:
            r = rows[article_type]
            out.append(
                {
                    "article_type": article_type,
                    "name": r.name,
                    "is_default": bool(r.is_default),
                    "is_system": False,
                    "target_word_count": r.target_word_count,
                    "sections": r.sections,
                    "format": r.format_options or dict(DEFAULT_FORMAT),
                }
            )
        else:
            out.append(get_system_template(article_type))
    return out


def extract_overrides(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    raw = state.get("blog_overrides") or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in (
        "target_word_count",
        "faq_count",
        "related_query_count",
        "include_site_cta",
        "auto_images",
        "creativity",
        "template_article_type",
        "intro_length",
        "conclusion_length",
        "cta_text",
        "cta_href",
        "seo_power_words",
        "last_updated_footer",
        "custom_style_notes",
    ):
        if key in raw and raw[key] is not None:
            out[key] = raw[key]
    return out


_YEAR_TOKEN = re.compile(r"\{year\}", re.I)


def substitute_section_tokens(sections: List[Dict[str, Any]], name: str, topic: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
    from datetime import date

    yr = str(year or date.today().year)

    def sub(text: str) -> str:
        t = _substitute_name(text or "", name)
        t = t.replace("{topic}", topic or name).replace("{Topic}", topic or name)
        return _YEAR_TOKEN.sub(yr, t)

    def walk(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for n in copy.deepcopy(nodes):
            n["heading"] = sub(str(n.get("heading") or ""))
            n["llm_brief"] = sub(str(n.get("llm_brief") or ""))
            n["children"] = walk(n.get("children") or [])
            out.append(n)
        return out

    return walk(sections)
