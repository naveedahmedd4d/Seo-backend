"""Free IT/AI news feeds: rank 10 stories, let the user's LLM pick 3, publish one blog."""

from __future__ import annotations

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.news_topic_entry import NewsTopicEntry
from app.models.user import User
from app.models.user_llm_settings import UserLlmSettings
from app.models.wordpress_site import WordPressSite
from app.services.http_ssl import httpx_verify
from app.services.llm_settings_service import get_or_create_settings

logger = logging.getLogger(__name__)

NEWS_SOURCES: list[dict[str, str]] = [
    {
        "id": "google_ai",
        "label": "Google News · AI",
        "description": "OpenAI, Anthropic, generative AI, and new models from the last day.",
        "url": (
            "https://news.google.com/rss/search?q=(OpenAI%20OR%20Anthropic%20OR%20"
            "%22generative%20AI%22%20OR%20ChatGPT%20OR%20%22AI%20model%22)%20when:1d"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    },
    {
        "id": "google_it",
        "label": "Google News · IT",
        "description": "Security, cloud, and major software news from the last day.",
        "url": (
            "https://news.google.com/rss/search?q=(cybersecurity%20OR%20%22cloud%20computing%22"
            "%20OR%20%22data%20breach%22%20OR%20%22software%20outage%22)%20when:1d"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    },
    {
        "id": "techcrunch",
        "label": "TechCrunch AI",
        "description": "TechCrunch artificial intelligence coverage.",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
    },
    {
        "id": "verge",
        "label": "The Verge AI",
        "description": "The Verge AI section.",
        "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    },
    {
        "id": "ars",
        "label": "Ars Technica",
        "description": "Ars Technica technology lab.",
        "url": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    },
    {
        "id": "hn",
        "label": "Hacker News",
        "description": "Current Hacker News front page.",
        "url": "https://hnrss.org/frontpage",
    },
]

SOURCE_IDS = [source["id"] for source in NEWS_SOURCES]
_SOURCE_BY_ID = {source["id"]: source for source in NEWS_SOURCES}

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "over", "after",
    "before", "about", "your", "their", "what", "when", "where", "which", "will",
    "have", "has", "had", "are", "was", "were", "been", "being", "its", "it's",
    "new", "news", "says", "said", "how", "why", "who", "can", "could", "just",
    "more", "than", "into", "onto", "per", "via", "not", "but", "you", "our",
    "his", "her", "they", "them", "out", "off", "all", "any", "now", "today",
}

_TITLE_SOURCE_SUFFIX = re.compile(r"\s+[-–|]\s+[^-–|]{2,60}$")


def sources_catalog() -> list[dict[str, str]]:
    return [
        {"id": source["id"], "label": source["label"], "description": source["description"]}
        for source in NEWS_SOURCES
    ]


def normalize_source_ids(raw: Optional[list[Any]]) -> list[str]:
    """Empty or missing selection means every feed is on."""
    if not raw:
        return list(SOURCE_IDS)
    chosen = []
    for item in raw:
        source_id = str(item).strip()
        if source_id in _SOURCE_BY_ID and source_id not in chosen:
            chosen.append(source_id)
    if not chosen:
        raise ValueError("Turn on at least one news source")
    return chosen


def clean_title(title: str) -> str:
    text = html.unescape(title or "")
    text = re.sub(r"\s+", " ", text).strip()
    return _TITLE_SOURCE_SUFFIX.sub("", text).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed_xml(xml_text: str, source_id: str, limit: int = 25) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = ""
        link = ""
        for child in list(el):
            name = _local(child.tag)
            if name == "title" and not title:
                title = clean_title("".join(child.itertext()))
            elif name == "link" and not link:
                link = (child.attrib.get("href") or "".join(child.itertext())).strip()
        if title:
            items.append({"title": title, "link": link, "source": source_id})
        if len(items) >= limit:
            break
    return items


def _tokens(title: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+'.-]{2,}", title.lower())
    return {word for word in words if word not in _STOPWORDS and not word.isdigit()}


def _same_story(left: set[str], right: set[str]) -> bool:
    """Match a headline to a cluster's original title, not to words picked up later."""
    overlap = left & right
    if len(overlap) < 2 and not any(len(word) >= 8 for word in overlap):
        return False
    smaller = min(len(left), len(right))
    return smaller > 0 and (len(overlap) / smaller) >= 0.5


def rank_stories(items: list[dict[str, str]], limit: int = 10) -> list[dict[str, Any]]:
    """Merge near-duplicate headlines, then keep the stories seen on the most feeds."""
    clusters: list[dict[str, Any]] = []
    for item in items:
        tokens = _tokens(item.get("title") or "")
        if not tokens:
            continue
        placed = False
        for cluster in clusters:
            if _same_story(tokens, cluster["seed_tokens"]):
                cluster["items"].append(item)
                cluster["sources"].add(item["source"])
                placed = True
                break
        if not placed:
            clusters.append(
                {
                    "title": item["title"],
                    "link": item.get("link") or "",
                    "items": [item],
                    "seed_tokens": set(tokens),
                    "sources": {item["source"]},
                }
            )
    clusters.sort(key=lambda cluster: (len(cluster["sources"]), len(cluster["items"])), reverse=True)
    ranked = []
    for index, cluster in enumerate(clusters[:limit], start=1):
        ranked.append(
            {
                "id": index,
                "title": cluster["title"],
                "link": cluster["link"],
                "sources": sorted(cluster["sources"]),
                "headline_count": len(cluster["items"]),
            }
        )
    return ranked


def parse_llm_pick(raw: str, valid_ids: set[int]) -> dict[str, Any]:
    """Accept only story ids that were in the ranked list."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        raise ValueError("The model did not return a topic choice")
    data = json.loads(match.group(0))
    picks: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in data.get("picks") or []:
        if not isinstance(item, dict):
            continue
        try:
            story_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if story_id not in valid_ids or story_id in seen:
            continue
        seen.add(story_id)
        picks.append({"id": story_id, "angle": str(item.get("angle") or "").strip()[:300]})
        if len(picks) == 3:
            break
    if not picks:
        raise ValueError("The model did not choose a story from the list")
    try:
        publish_id = int(data.get("publish_id"))
    except (TypeError, ValueError):
        publish_id = picks[0]["id"]
    if publish_id not in {pick["id"] for pick in picks}:
        publish_id = picks[0]["id"]
    return {
        "picks": picks,
        "publish_id": publish_id,
        "why": str(data.get("why") or "").strip()[:400],
    }


def choose_social_publish_id(
    stories: list[dict[str, Any]],
    *,
    blog_publish_id: Optional[int],
    picks: Optional[list[dict[str, Any]]] = None,
) -> Optional[int]:
    """Pick a social trending topic id that is never the blog's publish_id."""
    if not stories:
        return None
    ranked_ids = [int(story["id"]) for story in stories if story.get("id") is not None]
    if not ranked_ids:
        return None
    exclude = int(blog_publish_id) if blog_publish_id is not None else None
    pick_ids: list[int] = []
    for item in picks or []:
        try:
            pick_id = int(item.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if pick_id in ranked_ids and pick_id not in pick_ids:
            pick_ids.append(pick_id)
    # Prefer another LLM pick, then the next ranked story.
    for candidate in pick_ids + ranked_ids:
        if exclude is None or candidate != exclude:
            return candidate
    return None


def social_topic_from_stories(
    stories: list[dict[str, Any]],
    social_publish_id: Optional[int],
) -> Optional[dict[str, Any]]:
    if social_publish_id is None:
        return None
    story = next((item for item in stories if item.get("id") == social_publish_id), None)
    if not story:
        return None
    return {
        "id": story["id"],
        "title": story.get("title") or "",
        "link": story.get("link"),
        "sources": list(story.get("sources") or []),
    }


def _pick_prompt(stories: list[dict[str, Any]]) -> str:
    lines = [
        f"{story['id']}. {story['title']} (sources: {', '.join(story['sources'])})"
        for story in stories
    ]
    return (
        "These are today's IT and AI news stories. Choose exactly 3 for a blog and social audience, "
        "then choose 1 of those 3 to publish as today's single blog.\n"
        "Prefer stories confirmed by more than one source and a clear explanation angle. "
        "Skip pure celebrity gossip, local outages, and stories that will be stale tomorrow.\n"
        "Use only ids from this list.\n\n"
        "Stories:\n"
        + "\n".join(lines)
        + "\n\nReturn JSON only:\n"
        '{"picks":[{"id":1,"angle":"short blog angle"}],"publish_id":1,"why":"why this one blog today"}'
    )


async def _fetch_enabled_items(source_ids: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    errors: list[str] = []
    async with httpx.AsyncClient(
        timeout=20.0,
        verify=httpx_verify(),
        headers={"User-Agent": "SeoAutomation/1.0"},
        follow_redirects=True,
    ) as client:
        for source_id in source_ids:
            source = _SOURCE_BY_ID[source_id]
            try:
                response = await client.get(source["url"])
                response.raise_for_status()
                items.extend(parse_feed_xml(response.text, source_id))
            except Exception as exc:
                logger.warning("News source %s failed: %s", source_id, exc)
                errors.append(source_id)
    if not items:
        failed = ", ".join(errors) or "all sources"
        raise ValueError(f"No stories returned ({failed})")
    return items


def _today_key(moment: Optional[datetime] = None) -> str:
    current = moment or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).date().isoformat()


def _already_published_today(payload: Optional[dict], day: str) -> bool:
    if not isinstance(payload, dict):
        return False
    blog = payload.get("blog") if isinstance(payload.get("blog"), dict) else {}
    return payload.get("fetched_on") == day and blog.get("status") == "published"


_SOURCE_LABELS = {source["id"]: source["label"] for source in NEWS_SOURCES}


def topic_entry_rows(day: str, stories: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One table row per feed that mentioned a story."""
    rows: list[dict[str, str]] = []
    for story in stories:
        title = str(story.get("title") or "").strip()
        if not title:
            continue
        sources = story.get("sources") or []
        if not sources:
            sources = ["unknown"]
        for source_id in sources:
            source = str(source_id)
            rows.append(
                {
                    "topic_date": day,
                    "title": title,
                    "name": _SOURCE_LABELS.get(source, source),
                    "source": source,
                    "link": str(story.get("link") or ""),
                }
            )
    return rows


def _replace_topic_entries(db: Session, user_id: int, day: str, stories: list[dict[str, Any]]) -> None:
    topic_day = date.fromisoformat(day)
    db.query(NewsTopicEntry).filter(
        NewsTopicEntry.user_id == user_id,
        NewsTopicEntry.topic_date == topic_day,
    ).delete(synchronize_session=False)
    for item in topic_entry_rows(day, stories):
        db.add(
            NewsTopicEntry(
                user_id=user_id,
                topic_date=topic_day,
                title=item["title"],
                name=item["name"],
                source=item["source"],
                link=item["link"] or None,
            )
        )


def list_topic_entries(db: Session, user_id: int) -> list[NewsTopicEntry]:
    """Return saved daily topics, newest day first. Backfill from the last run if needed."""
    rows = (
        db.query(NewsTopicEntry)
        .filter(NewsTopicEntry.user_id == user_id)
        .order_by(NewsTopicEntry.topic_date.desc(), NewsTopicEntry.id.asc())
        .all()
    )
    if rows:
        return rows
    settings_row = get_or_create_settings(db, user_id)
    last_run = settings_row.news_topics if isinstance(settings_row.news_topics, dict) else None
    stories = (last_run or {}).get("top10") if isinstance(last_run, dict) else None
    day = (last_run or {}).get("fetched_on") if isinstance(last_run, dict) else None
    if not day or not isinstance(stories, list) or not stories:
        return []
    _replace_topic_entries(db, user_id, str(day), stories)
    db.commit()
    return (
        db.query(NewsTopicEntry)
        .filter(NewsTopicEntry.user_id == user_id)
        .order_by(NewsTopicEntry.topic_date.desc(), NewsTopicEntry.id.asc())
        .all()
    )


def settings_payload(row: UserLlmSettings) -> dict[str, Any]:
    sources = row.news_topic_sources if isinstance(row.news_topic_sources, list) else list(SOURCE_IDS)
    sources = [source_id for source_id in sources if source_id in _SOURCE_BY_ID] or list(SOURCE_IDS)
    last_run = row.news_topics if isinstance(row.news_topics, dict) else None
    return {
        "sources": sources_catalog(),
        "enabled_sources": sources,
        "daily_blog_enabled": bool(row.news_daily_blog_enabled),
        "wordpress_site_id": row.news_wordpress_site_id,
        "last_run": last_run,
    }


def update_news_settings(
    db: Session,
    user_id: int,
    *,
    enabled_sources: Optional[list[str]] = None,
    daily_blog_enabled: Optional[bool] = None,
    wordpress_site_id: Optional[int] = None,
    clear_wordpress_site: bool = False,
) -> UserLlmSettings:
    row = get_or_create_settings(db, user_id)
    if enabled_sources is not None:
        row.news_topic_sources = normalize_source_ids(enabled_sources)
    if daily_blog_enabled is not None:
        row.news_daily_blog_enabled = bool(daily_blog_enabled)
    if clear_wordpress_site:
        row.news_wordpress_site_id = None
    elif wordpress_site_id is not None:
        site = (
            db.query(WordPressSite)
            .filter(WordPressSite.id == wordpress_site_id, WordPressSite.user_id == user_id)
            .first()
        )
        if not site:
            raise ValueError("WordPress site not found")
        row.news_wordpress_site_id = site.id
    db.commit()
    db.refresh(row)
    return row


async def _choose_topics(db: Session, user_id: int, stories: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services.llm_settings_service import resolve_llm_credentials
    from app.services.openrouter_service import generate_content, reset_llm_context, set_llm_context

    token = set_llm_context(resolve_llm_credentials(db, user_id))
    try:
        raw = await generate_content(
            _pick_prompt(stories),
            max_tokens=700,
            temperature=0.2,
            system="You choose blog topics from a supplied list. Return only JSON.",
        )
    finally:
        reset_llm_context(token)
    return parse_llm_pick(raw, {int(story["id"]) for story in stories})


async def _publish_chosen_blog(
    db: Session,
    user: User,
    row: UserLlmSettings,
    stories: list[dict[str, Any]],
    choice: dict[str, Any],
) -> dict[str, Any]:
    from app.api.blog import publish_blog, run_blog_workflow
    from app.models.workflow_state import WorkflowState, WorkflowStatus, WorkflowType
    from app.schemas.blog import BlogPublishRequest
    from app.services.openrouter_service import TECHNOLOGY_AI_CATEGORY

    if not row.news_wordpress_site_id:
        return {"status": "skipped", "error": "Choose a WordPress site before the daily blog can publish"}
    site = (
        db.query(WordPressSite)
        .filter(WordPressSite.id == row.news_wordpress_site_id, WordPressSite.user_id == user.id)
        .first()
    )
    if not site:
        return {"status": "skipped", "error": "WordPress site not found"}

    story = next((item for item in stories if item["id"] == choice["publish_id"]), None)
    if not story:
        return {"status": "failed", "error": "Chosen story is not in today's list"}
    angle = next(
        (pick.get("angle") for pick in choice["picks"] if pick["id"] == story["id"]),
        "",
    )
    headline_block = "\n".join(
        f"- {item['title']} ({', '.join(item['sources'])})" for item in stories[:10]
    )
    news_context = (
        f"Today's chosen topic: {story['title']}\n"
        f"Angle: {angle or choice.get('why') or story['title']}\n"
        f"Source link: {story.get('link') or ''}\n\n"
        f"Other headlines considered:\n{headline_block}"
    )
    workflow = WorkflowState(
        user_id=user.id,
        workflow_type=WorkflowType.BLOG,
        status=WorkflowStatus.PENDING,
        state_data={
            "topic": story["title"],
            "description": angle or choice.get("why"),
            "keywords": [story["title"]],
            "category": TECHNOLOGY_AI_CATEGORY,
            "source": "news_topics",
        },
    )
    db.add(workflow)
    db.commit()
    db.refresh(workflow)
    await run_blog_workflow(
        db,
        workflow.id,
        story["title"],
        angle or choice.get("why"),
        [story["title"]],
        TECHNOLOGY_AI_CATEGORY,
        user.id,
        include_research=True,
        news_context=news_context,
    )
    db.refresh(workflow)
    if workflow.status != WorkflowStatus.COMPLETED:
        return {
            "status": "failed",
            "workflow_id": workflow.id,
            "error": workflow.error_message or "Blog generation failed",
        }
    publish_response = await publish_blog(
        BlogPublishRequest(
            workflow_id=workflow.id,
            wordpress_site_id=site.id,
            status="publish",
            source_category=TECHNOLOGY_AI_CATEGORY,
        ),
        current_user=user,
        db=db,
    )
    published = publish_response.model_dump(mode="json")
    state = workflow.state_data if isinstance(workflow.state_data, dict) else {}
    title = state.get("title") or story["title"]
    body = state.get("body_content") or ""
    meta = state.get("meta_description") or ""
    featured = state.get("featured_image")
    image_url = featured.get("url") if isinstance(featured, dict) else featured
    link = published.get("link")

    share: dict[str, Any] = {"status": "skipped", "reason": "share_toggles_off"}
    try:
        from app.services.weekly_content_service import (
            _generate_and_publish_social,
            get_or_create_settings as get_weekly_settings,
            platforms_for_blog_share,
        )

        weekly = get_weekly_settings(db, user.id)
        platforms = platforms_for_blog_share(db, user.id, weekly)
        if platforms:
            history, results = await _generate_and_publish_social(
                db,
                user,
                query=story["title"],
                source_title=title,
                source_snippet=meta or (body[:800] if isinstance(body, str) else ""),
                source_url=link,
                platforms=platforms,
                settings_snapshot={
                    "source": "news_topics_blog",
                    "story_id": story["id"],
                    "platforms": platforms,
                    "match_blog": True,
                },
                image_url=image_url if isinstance(image_url, str) else None,
                body_html=body if isinstance(body, str) else None,
                match_blog=True,
            )
            share = {
                "status": "shared",
                "social_post_history_id": history.id,
                "platforms": platforms,
                "results": results,
            }
    except Exception as exc:
        logger.exception("News blog share failed for user %s", user.id)
        share = {"status": "failed", "error": str(exc)[:500]}

    return {
        "status": "published",
        "workflow_id": workflow.id,
        "post_id": published.get("post_id"),
        "link": link,
        "title": title,
        "story_id": story["id"],
        "share": share,
    }


async def run_news_topics_for_user(
    db: Session,
    user: User,
    *,
    publish: Optional[bool] = None,
) -> dict[str, Any]:
    """Fetch enabled feeds, rank 10, ask the user's model for 3, and publish one when due."""
    row = get_or_create_settings(db, user.id)
    source_ids = normalize_source_ids(
        row.news_topic_sources if isinstance(row.news_topic_sources, list) else None
    )
    day = _today_key()
    should_publish = row.news_daily_blog_enabled if publish is None else publish
    already_published = _already_published_today(row.news_topics, day)
    previous_blog = None
    if already_published and isinstance(row.news_topics, dict):
        previous_blog = row.news_topics.get("blog")

    items = await _fetch_enabled_items(source_ids)
    stories = rank_stories(items)
    if not stories:
        raise ValueError("No stories returned")

    choice: dict[str, Any] = {"picks": [], "publish_id": None, "why": ""}
    pick_error = None
    try:
        choice = await _choose_topics(db, user.id, stories)
    except Exception as exc:
        logger.exception("News topic pick failed for user %s", user.id)
        pick_error = str(exc)[:500]

    blog: dict[str, Any] = {"status": "not_requested"}
    if already_published and isinstance(previous_blog, dict):
        blog = previous_blog
    elif should_publish and choice.get("publish_id"):
        try:
            blog = await _publish_chosen_blog(db, user, row, stories, choice)
        except Exception as exc:
            logger.exception("News topic blog failed for user %s", user.id)
            blog = {"status": "failed", "error": str(exc)[:500]}
    elif should_publish and pick_error:
        blog = {"status": "skipped", "error": "No topic was chosen, so nothing was published"}

    # Reload in case blog generation committed other changes on this session.
    row = get_or_create_settings(db, user.id)
    blog_publish_id = (
        (previous_blog or {}).get("story_id")
        if already_published and isinstance(previous_blog, dict)
        else choice.get("publish_id")
    )
    previous_social_id = None
    previous_social_topic = None
    if isinstance(row.news_topics, dict) and row.news_topics.get("fetched_on") == day:
        previous_social_id = row.news_topics.get("social_publish_id")
        previous_social_topic = row.news_topics.get("social_topic")
    blog_id = blog_publish_id if isinstance(blog_publish_id, int) else None
    social_publish_id = previous_social_id if isinstance(previous_social_id, int) else None
    # Always keep social ≠ blog, even if a later publish run changes publish_id.
    if social_publish_id is None or (blog_id is not None and social_publish_id == blog_id):
        social_publish_id = choose_social_publish_id(
            stories,
            blog_publish_id=blog_id,
            picks=choice.get("picks") or [],
        )
        previous_social_topic = None
    social_topic = previous_social_topic or social_topic_from_stories(stories, social_publish_id)
    row.news_topics = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetched_on": day,
        "enabled_sources": source_ids,
        "top10": stories,
        "picks": choice.get("picks") or [],
        "publish_id": blog_publish_id,
        "social_publish_id": social_publish_id,
        "social_topic": social_topic,
        "why": choice.get("why") or "",
        "pick_error": pick_error,
        "blog": blog,
    }
    _replace_topic_entries(db, user.id, day, stories)
    db.commit()
    db.refresh(row)
    return settings_payload(row)


async def run_due_news_topics(*, force: bool = False) -> int:
    """Publish today's blog for users who turned the daily blog on. One post per user per day."""
    from app.database import SessionLocal

    db = SessionLocal()
    ran = 0
    day = _today_key()
    try:
        rows = (
            db.query(UserLlmSettings)
            .filter(UserLlmSettings.news_daily_blog_enabled.is_(True))
            .all()
        )
        for row in rows:
            if not force and _already_published_today(row.news_topics, day):
                continue
            user = db.query(User).filter(User.id == row.user_id).first()
            if not user:
                continue
            try:
                await run_news_topics_for_user(db, user, publish=True)
                ran += 1
            except Exception:
                logger.exception("Daily news topics failed for user %s", row.user_id)
                db.rollback()
        return ran
    finally:
        db.close()
