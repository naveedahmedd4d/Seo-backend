from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.bot_automation import (
    AutomationApproval,
    AutomationBot,
    AutomationEvent,
    AutomationExecution,
    AutomationNodeExecution,
    AutomationNotification,
)
from app.models.reddit_trends import RedditTrend
from app.models.social_post_history import SocialPostHistory
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.models.workflow_state import WorkflowState, WorkflowStatus, WorkflowType

logger = logging.getLogger(__name__)

NODE_TYPES = {"trigger", "trending_keywords", "research", "blog_generation", "social_media"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_workflow_definition() -> Dict[str, Any]:
    nodes = [
        {"id": "trigger", "type": "trigger", "label": "Trigger", "enabled": True, "position": {"x": 40, "y": 180}, "config": {"mode": "manual"}},
        {"id": "keywords", "type": "trending_keywords", "label": "Trending Keywords", "enabled": True, "position": {"x": 330, "y": 180}, "config": {"source": "trending", "hours": 4, "limit": 3, "deduplicate": True}},
        {"id": "research", "type": "research", "label": "Research", "enabled": True, "position": {"x": 620, "y": 180}, "config": {"depth": "basic", "save_citations": True}},
        {"id": "blog", "type": "blog_generation", "label": "Blog Generation", "enabled": True, "position": {"x": 910, "y": 180}, "config": {"category": "Technology & AI", "target_word_count": 1400, "publication_mode": "draft"}},
        {"id": "social", "type": "social_media", "label": "Social Media", "enabled": True, "position": {"x": 1200, "y": 180}, "config": {"platforms": ["linkedin", "facebook", "instagram", "x"], "publication_mode": "draft", "hashtag_count": 12}},
    ]
    return {
        "nodes": nodes,
        "edges": [
            {"id": "trigger-keywords", "source": "trigger", "target": "keywords"},
            {"id": "keywords-research", "source": "keywords", "target": "research"},
            {"id": "research-blog", "source": "research", "target": "blog"},
            {"id": "blog-social", "source": "blog", "target": "social"},
        ],
    }


def validate_workflow(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        raise ValueError("Workflow must contain at least one node")
    ids = {str(node.get("id")) for node in nodes}
    if len(ids) != len(nodes) or "None" in ids:
        raise ValueError("Workflow node ids must be present and unique")
    for node in nodes:
        if node.get("type") not in NODE_TYPES:
            raise ValueError(f"Unsupported workflow node type: {node.get('type')}")
    incoming: Dict[str, int] = {node_id: 0 for node_id in ids}
    outgoing: Dict[str, List[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source not in ids or target not in ids:
            raise ValueError("Every workflow edge must connect existing nodes")
        outgoing[source].append(target)
        incoming[target] += 1
    queue = [node_id for node_id, count in incoming.items() if count == 0]
    ordered_ids: List[str] = []
    while queue:
        node_id = queue.pop(0)
        ordered_ids.append(node_id)
        for target in outgoing[node_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    if len(ordered_ids) != len(nodes):
        raise ValueError("Workflow contains a cycle")
    by_id = {str(node["id"]): node for node in nodes}
    return [by_id[node_id] for node_id in ordered_ids]


def next_run_at(
    schedule: Dict[str, Any],
    now: Optional[datetime] = None,
    timezone_name: str = "UTC",
) -> Optional[datetime]:
    now = now or utcnow()
    mode = schedule.get("mode", "manual")
    if mode == "manual":
        return None
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc
    local_now = now.astimezone(local_tz)
    hour, minute = 9, 0
    try:
        hour, minute = [int(part) for part in str(schedule.get("time", "09:00")).split(":", 1)]
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Schedule time must use HH:MM in 24-hour format")
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if mode == "daily":
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)
    if mode == "weekly":
        weekday = max(0, min(6, int(schedule.get("weekday", 0))))
        candidate += timedelta(days=(weekday - local_now.weekday()) % 7)
        if candidate <= local_now:
            candidate += timedelta(days=7)
        return candidate.astimezone(timezone.utc)
    if mode == "interval":
        amount = max(1, int(schedule.get("interval", 1)))
        unit = schedule.get("unit", "hours")
        return now + (timedelta(minutes=amount) if unit == "minutes" else timedelta(hours=amount))
    raise ValueError("Schedule mode must be manual, daily, weekly, or interval")


def ensure_site(db: Session, user_id: int, site_id: int) -> WordPressSite:
    site = db.query(WordPressSite).filter(
        WordPressSite.id == site_id, WordPressSite.user_id == user_id
    ).first()
    if not site:
        raise HTTPException(status_code=404, detail="WordPress site not found")
    return site


def create_execution(
    db: Session,
    bot: AutomationBot,
    *,
    trigger_type: str = "manual",
    start_node_id: Optional[str] = None,
    resumed_from: Optional[AutomationExecution] = None,
    idempotency_key: Optional[str] = None,
) -> AutomationExecution:
    ordered = validate_workflow(bot.workflow_definition)
    if start_node_id and start_node_id not in {str(node["id"]) for node in ordered}:
        raise ValueError("Start node does not exist in this workflow")
    execution = AutomationExecution(
        bot_id=bot.id,
        user_id=bot.user_id,
        wordpress_site_id=bot.wordpress_site_id,
        trigger_type=trigger_type,
        status="queued",
        graph_snapshot=bot.workflow_definition,
        settings_snapshot=bot.settings or {},
        start_node_id=start_node_id,
        resumed_from_execution_id=resumed_from.id if resumed_from else None,
        idempotency_key=idempotency_key,
    )
    db.add(execution)
    db.flush()
    resume_reached = not bool(start_node_id)
    previous = {}
    if resumed_from:
        previous = {row.node_id: row for row in resumed_from.node_executions if row.status == "completed"}
    for position, node in enumerate(ordered):
        node_id = str(node["id"])
        if start_node_id == node_id:
            resume_reached = True
        copied = previous.get(node_id) if not resume_reached else None
        initial_status = "completed" if copied else ("disabled" if not node.get("enabled", True) else "pending")
        if start_node_id and not resume_reached and not copied:
            initial_status = "skipped"
        db.add(AutomationNodeExecution(
            execution_id=execution.id,
            user_id=bot.user_id,
            node_id=node_id,
            node_type=str(node["type"]),
            position=position,
            status=initial_status,
            input_data=copied.input_data if copied else None,
            output_data=copied.output_data if copied else None,
            usage_data={"checkpoint_reused": True} if copied else None,
            completed_at=utcnow() if copied else None,
        ))
    _event(db, execution, "execution_queued", "Workflow execution queued")
    db.commit()
    db.refresh(execution)
    return execution


def _event(
    db: Session,
    execution: AutomationExecution,
    event_type: str,
    message: str,
    *,
    node: Optional[AutomationNodeExecution] = None,
    level: str = "info",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    db.add(AutomationEvent(
        execution_id=execution.id,
        node_execution_id=node.id if node else None,
        user_id=execution.user_id,
        level=level,
        event_type=event_type,
        message=message,
        details=details,
    ))


class ApprovalPending(Exception):
    def __init__(self, output: Dict[str, Any]):
        super().__init__("Approval required")
        self.output = output


def _approval_gate(
    db: Session,
    execution: AutomationExecution,
    node: AutomationNodeExecution,
    output: Dict[str, Any],
) -> None:
    approval = db.query(AutomationApproval).filter(
        AutomationApproval.execution_id == execution.id,
        AutomationApproval.node_id == node.node_id,
    ).first()
    if approval and approval.status == "approved":
        return
    if approval and approval.status == "rejected":
        raise ValueError("Publication was rejected")
    if not approval:
        db.add(AutomationApproval(
            execution_id=execution.id,
            user_id=execution.user_id,
            node_id=node.node_id,
        ))
        db.flush()
    raise ApprovalPending(output)


def _predecessor_output(db: Session, execution_id: int, node_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
    sources = [edge.get("source") for edge in graph.get("edges", []) if edge.get("target") == node_id]
    if not sources:
        return {}
    rows = db.query(AutomationNodeExecution).filter(
        AutomationNodeExecution.execution_id == execution_id,
        AutomationNodeExecution.node_id.in_(sources),
    ).all()
    if len(rows) == 1:
        return rows[0].output_data or {}
    return {row.node_id: row.output_data or {} for row in rows}


async def _run_trigger(config: Dict[str, Any], execution: AutomationExecution) -> Dict[str, Any]:
    return {"triggered_at": utcnow().isoformat(), "mode": execution.trigger_type, "config": config}


async def _run_keywords(
    db: Session, user: User, execution: AutomationExecution, config: Dict[str, Any]
) -> Dict[str, Any]:
    source = str(config.get("source", "trending"))
    limit = max(1, min(int(config.get("limit", 3)), int((execution.settings_snapshot or {}).get("max_articles_per_run", 10))))
    if source == "reddit":
        rows = db.query(RedditTrend).filter(
            RedditTrend.user_id == user.id, RedditTrend.status == "active"
        ).order_by(RedditTrend.trend_score.desc()).limit(limit * 3).all()
        keywords = [{
            "id": row.trending_keyword_id,
            "keyword": row.topic,
            "category": row.category,
            "value": row.engagement_total,
            "kd": None,
            "source": "reddit",
            "coverage": None,
        } for row in rows]
    else:
        from app.api.trending import get_trending_keywords

        response = await get_trending_keywords(
            refresh=bool(config.get("refresh", False)),
            hours=int(config.get("hours", 4)),
            category=config.get("category"),
            completed_only=False,
            source=source,
            language=str(config.get("language", "all")),
            wordpress_site_id=(
                execution.wordpress_site_id
                if execution.wordpress_site_id is not None and config.get("coverage_status")
                else None
            ),
            coverage_status=(
                config.get("coverage_status")
                if execution.wordpress_site_id is not None
                else None
            ),
            current_user=user,
            db=db,
        )
        keywords = [item.model_dump(mode="json") for item in response.keywords]
    seen = set()
    selected = []
    min_volume = int(config.get("min_search_volume", 0) or 0)
    max_kd = config.get("max_kd")
    relevance_terms = str(config.get("relevance_terms") or "").strip()
    if not relevance_terms:
        global_topics = (execution.settings_snapshot or {}).get("preferred_topics", "")
        global_industry = (execution.settings_snapshot or {}).get("industry", "")
        relevance_terms = f"{global_topics},{global_industry}".strip(",")
    relevance_tokens = {
        token.casefold().strip() for token in relevance_terms.replace(";", ",").split(",") if token.strip()
    }
    for item in keywords:
        name = str(item.get("keyword") or "").strip()
        key = name.casefold()
        if not name or (config.get("deduplicate", True) and key in seen):
            continue
        if config.get("relevance_filter") and relevance_tokens:
            category_text = str(item.get("category") or "").casefold()
            if not any(token in key or token in category_text for token in relevance_tokens):
                continue
        if min_volume and int(item.get("value") or 0) < min_volume:
            continue
        if max_kd is not None and item.get("kd") is not None and float(item["kd"]) > float(max_kd):
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    if not selected:
        raise ValueError("No keywords matched the relevance, coverage, volume, and difficulty filters")
    return {"keywords": selected, "count": len(selected), "source": source}


async def _run_research(
    db: Session, user: User, input_data: Dict[str, Any], config: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.llm_settings_service import resolve_llm_credentials
    from app.services.openrouter_service import (
        generate_research_content_summary,
        reset_llm_context,
        set_llm_context,
    )
    from app.services.trivly_service import research_keywords

    researched = []
    token = set_llm_context(resolve_llm_credentials(db, user.id))
    try:
        for item in input_data.get("keywords", []):
            keyword = str(item.get("keyword") or "").strip()
            raw = await research_keywords(db, keyword, user.id)
            results = raw.get("results") or []
            summary = await generate_research_content_summary(
                query=keyword,
                results=results,
                topical_mapping=None,
                competitors=[],
                opportunities=[],
                category=item.get("category") or config.get("category"),
                celebrity_content_type=None,
                content_intent=config.get("content_intent"),
                cluster_subs=None,
            )
            researched.append({
                "keyword": item,
                "research": raw,
                "summary": summary,
                "citations": [{"title": row.get("title"), "url": row.get("url")} for row in results if row.get("url")],
                "target_audience": config.get("target_audience"),
                "country": config.get("country"),
                "language": config.get("language", "English"),
            })
    finally:
        reset_llm_context(token)
    return {"items": researched, "count": len(researched)}


def _summary_context(summary: Dict[str, Any]) -> str:
    parts = [str(summary.get("summary") or "")]
    outline = summary.get("outline") or []
    if outline:
        parts.append("Recommended outline:\n" + "\n".join(
            f"{'#' * int(row.get('level', 2))} {row.get('text', '')}" for row in outline if isinstance(row, dict)
        ))
    return "\n\n".join(part for part in parts if part).strip()


async def _run_blog(
    db: Session,
    user: User,
    execution: AutomationExecution,
    node: AutomationNodeExecution,
    input_data: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    from app.api.blog import publish_blog, run_blog_workflow
    from app.schemas.blog import BlogPublishRequest

    cached = node.output_data or {}
    articles = list(cached.get("articles") or [])
    if not articles:
        for item in input_data.get("items", []):
            keyword_data = item.get("keyword") or {}
            topic = str(keyword_data.get("keyword") or "").strip()
            summary = item.get("summary") or {}
            workflow = WorkflowState(
                user_id=user.id,
                workflow_type=WorkflowType.BLOG,
                status=WorkflowStatus.PENDING,
                state_data={
                    "topic": topic,
                    "description": _summary_context(summary),
                    "keywords": summary.get("target_keywords") or [topic],
                    "category": config.get("category") or keyword_data.get("category") or "Technology & AI",
                    "source": "bot_automation",
                    "automation_execution_id": execution.id,
                    "blog_overrides": {
                        key: value for key, value in {
                            "target_word_count": config.get("target_word_count"),
                            "faq_count": config.get("faq_count"),
                            "related_query_count": config.get("related_query_count"),
                            "include_site_cta": config.get("include_site_cta"),
                            "auto_images": config.get("auto_images"),
                            "creativity": config.get("creativity"),
                            "template_article_type": config.get("content_type"),
                        }.items() if value is not None
                    },
                },
            )
            db.add(workflow)
            db.commit()
            db.refresh(workflow)
            await run_blog_workflow(
                db,
                workflow.id,
                topic,
                _summary_context(summary),
                summary.get("target_keywords") or [topic],
                config.get("category") or keyword_data.get("category") or "Technology & AI",
                user.id,
                include_research=False,
                suggested_title=summary.get("h1") or None,
                suggested_meta_description=summary.get("news_meta_description") or None,
                news_context=summary.get("news_context") or None,
                content_intent=config.get("content_intent"),
            )
            db.refresh(workflow)
            if workflow.status != WorkflowStatus.COMPLETED:
                raise ValueError(workflow.error_message or f"Blog generation failed for {topic}")
            if config.get("auto_images"):
                from app.services.blog_image_service import apply_auto_images_to_workflow

                await apply_auto_images_to_workflow(
                    db,
                    workflow,
                    user.id,
                    str(config.get("content_type") or "generic"),
                    set(),
                )
                db.refresh(workflow)
            state = workflow.state_data or {}
            articles.append({
                "workflow_id": workflow.id,
                "topic": topic,
                "title": state.get("title") or topic,
                "meta_description": state.get("meta_description"),
                "body_content": state.get("body_content"),
                "featured_image": state.get("featured_image"),
            })
        node.output_data = {"articles": articles, "count": len(articles)}
        db.commit()

    publication_mode = config.get("publication_mode", "draft")
    if execution.wordpress_site_id is None:
        return {"articles": articles, "count": len(articles), "published": []}
    if (execution.settings_snapshot or {}).get("approval_required") and publication_mode in {"publish", "schedule"}:
        publication_mode = "approval"
    if publication_mode == "approval":
        _approval_gate(db, execution, node, {"articles": articles, "count": len(articles)})
        publication_mode = str(config.get("approved_action", "draft"))
    if not cached.get("published") and publication_mode in {"draft", "publish", "schedule"}:
        published = []
        for article in articles:
            response = await publish_blog(
                BlogPublishRequest(
                    workflow_id=article["workflow_id"],
                    wordpress_site_id=execution.wordpress_site_id,
                    status="publish" if publication_mode == "publish" else "draft",
                    source_category=config.get("category"),
                ),
                current_user=user,
                db=db,
            )
            result = response.model_dump(mode="json")
            if publication_mode == "schedule":
                result.update({
                    "status": "scheduled",
                    "scheduled_for": config.get("scheduled_for"),
                    "note": "Saved as a WordPress draft for scheduled publishing",
                })
            published.append(result)
        cached["published"] = published
    return {"articles": articles, "count": len(articles), "published": cached.get("published", [])}


async def _run_social(
    db: Session,
    user: User,
    execution: AutomationExecution,
    node: AutomationNodeExecution,
    input_data: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    from app.services.llm_settings_service import resolve_llm_credentials
    from app.services.openrouter_service import (
        generate_news_headline,
        generate_social_post_description,
        generate_social_post_hashtags,
        generate_social_post_story_description,
        reset_llm_context,
        set_llm_context,
    )

    cached = node.output_data or {}
    posts = list(cached.get("posts") or [])
    if not posts:
        token = set_llm_context(resolve_llm_credentials(db, user.id))
        try:
            for article in input_data.get("articles", []):
                source_url = next((p.get("link") for p in input_data.get("published", []) if p.get("link")), None)
                source = [{
                    "title": article.get("title"),
                    "source": "Generated article",
                    "snippet": (article.get("meta_description") or article.get("body_content") or "")[:1200],
                    "link": source_url,
                    "date": utcnow().date().isoformat(),
                }]
                headline = await generate_news_headline(source, query=article.get("topic"))
                description = await generate_social_post_description(source, headline, article.get("topic"))
                story = await generate_social_post_story_description(source, headline, article.get("topic"))
                hashtags = await generate_social_post_hashtags(
                    source, headline, article.get("topic"), int(config.get("hashtag_count", 12))
                )
                history = SocialPostHistory(
                    user_id=user.id,
                    query=article.get("topic") or article.get("title") or "Generated article",
                    headline=headline,
                    description=description,
                    story_description=story,
                    hashtags=hashtags,
                    settings_snapshot={
                        "automation_execution_id": execution.id,
                        "platforms": config.get("platforms", []),
                        "tone": config.get("tone"),
                        "cta": config.get("cta"),
                    },
                    news_results=source,
                    publish_status="draft",
                )
                db.add(history)
                db.commit()
                db.refresh(history)
                featured = article.get("featured_image")
                image_url = featured.get("url") if isinstance(featured, dict) else featured
                posts.append({
                    "history_id": history.id,
                    "article_workflow_id": article.get("workflow_id"),
                    "headline": headline,
                    "description": description,
                    "story_description": story,
                    "hashtags": hashtags,
                    "platforms": config.get("platforms", []),
                    "source_url": source_url,
                    "image_url": image_url,
                })
        finally:
            reset_llm_context(token)
        node.output_data = {"posts": posts, "count": len(posts)}
        db.commit()
    publication_mode = config.get("publication_mode", "draft")
    if (execution.settings_snapshot or {}).get("approval_required") and publication_mode in {"publish", "schedule"}:
        publication_mode = "approval"
    if publication_mode == "approval":
        _approval_gate(db, execution, node, {"posts": posts, "count": len(posts)})
        publication_mode = str(config.get("approved_action", "publish"))
    publication_results = list(cached.get("publication_results") or [])
    if publication_mode == "publish" and not publication_results:
        from app.services.social_post_copy_service import PublishCopyContext
        from app.services.social_post_platform_publish import publish_to_platform
        from app.services.social_post_publish_service import finalize_history_status, record_publications

        for post in posts:
            history = db.query(SocialPostHistory).filter(
                SocialPostHistory.id == post["history_id"],
                SocialPostHistory.user_id == user.id,
            ).first()
            if not history:
                continue
            ctx = PublishCopyContext(
                headline=post["headline"],
                short_description=post.get("description"),
                story_caption=post.get("story_description"),
                hashtags=post.get("hashtags") or [],
                source_url=post.get("source_url"),
            )
            results = []
            for platform in post.get("platforms") or []:
                results.append(await publish_to_platform(
                    db,
                    user.id,
                    platform,
                    ctx=ctx,
                    image_url=post.get("image_url"),
                    platform_options=(config.get("platform_options") or {}).get(platform, {}),
                ))
            record_publications(db, user.id, history.id, results)
            publish_status = finalize_history_status(db, history, results)
            publication_results.append({
                "history_id": history.id,
                "status": publish_status,
                "platforms": [result.model_dump(mode="json") for result in results],
            })
    elif publication_mode == "schedule":
        publication_results = [{
            "history_id": post["history_id"],
            "status": "scheduled",
            "scheduled_for": config.get("scheduled_for"),
        } for post in posts]
    return {
        "posts": posts,
        "count": len(posts),
        "publication_mode": publication_mode,
        "publication_results": publication_results,
    }


async def _execute_node(
    db: Session,
    user: User,
    execution: AutomationExecution,
    node_row: AutomationNodeExecution,
    definition: Dict[str, Any],
    input_data: Dict[str, Any],
) -> Dict[str, Any]:
    config = definition.get("config") or {}
    node_type = node_row.node_type
    if node_type == "trigger":
        return await _run_trigger(config, execution)
    if node_type == "trending_keywords":
        return await _run_keywords(db, user, execution, config)
    if node_type == "research":
        return await _run_research(db, user, input_data, config)
    if node_type == "blog_generation":
        return await _run_blog(db, user, execution, node_row, input_data, config)
    if node_type == "social_media":
        return await _run_social(db, user, execution, node_row, input_data, config)
    raise ValueError(f"Unsupported node type: {node_type}")


async def run_execution(execution_id: int) -> None:
    db = SessionLocal()
    try:
        execution = db.query(AutomationExecution).filter(AutomationExecution.id == execution_id).first()
        if not execution or execution.status not in {"queued", "waiting_approval"}:
            return
        user = db.query(User).filter(User.id == execution.user_id).first()
        if not user:
            raise ValueError("Execution owner no longer exists")
        if execution.wordpress_site_id is not None:
            ensure_site(db, execution.user_id, execution.wordpress_site_id)
        ordered = validate_workflow(execution.graph_snapshot)
        definitions = {str(node["id"]): node for node in ordered}
        execution.status = "running"
        execution.started_at = execution.started_at or utcnow()
        execution.error_summary = None
        _event(db, execution, "execution_started", "Workflow execution started")
        db.commit()

        rows = db.query(AutomationNodeExecution).filter(
            AutomationNodeExecution.execution_id == execution.id
        ).order_by(AutomationNodeExecution.position).all()
        for row in rows:
            if row.status == "disabled":
                passthrough = _predecessor_output(db, execution.id, row.node_id, execution.graph_snapshot)
                row.input_data = passthrough
                row.output_data = passthrough
                row.completed_at = utcnow()
                _event(db, execution, "node_disabled", f"{definitions[row.node_id].get('label')} bypassed", node=row)
                db.commit()
                continue
            if row.status in {"completed", "skipped"}:
                continue
            definition = definitions[row.node_id]
            execution.current_node_id = row.node_id
            row.status = "running"
            row.attempt = int(row.attempt or 0) + 1
            row.started_at = utcnow()
            test_inputs = (execution.settings_snapshot or {}).get("__node_test_inputs", {})
            input_data = test_inputs.get(row.node_id) if execution.trigger_type == "node_test" else None
            if input_data is None:
                input_data = _predecessor_output(db, execution.id, row.node_id, execution.graph_snapshot)
            row.input_data = input_data
            _event(db, execution, "node_started", f"{definition.get('label')} started", node=row)
            db.commit()

            retries = max(0, int((execution.settings_snapshot or {}).get("max_retries", 1)))
            while True:
                try:
                    output = await _execute_node(db, user, execution, row, definition, input_data)
                    row.output_data = output
                    row.status = "completed"
                    row.completed_at = utcnow()
                    started = row.started_at
                    duration_ms = None
                    if started:
                        try:
                            duration_ms = int((row.completed_at - started).total_seconds() * 1000)
                        except TypeError:
                            duration_ms = None
                    item_count = int(output.get("count", 1) or 0) if isinstance(output, dict) else 1
                    api_multiplier = {"research": 2, "blog_generation": 1, "social_media": 4}.get(row.node_type, 1)
                    row.usage_data = {
                        "attempts": row.attempt,
                        "duration_ms": duration_ms,
                        "api_calls_estimate": item_count * api_multiplier,
                        "token_usage": None,
                    }
                    row.error_data = None
                    _event(db, execution, "node_completed", f"{definition.get('label')} completed", node=row)
                    db.commit()
                    break
                except ApprovalPending as pending:
                    row.output_data = pending.output
                    row.status = "waiting_approval"
                    execution.status = "waiting_approval"
                    _event(db, execution, "approval_requested", f"{definition.get('label')} is waiting for approval", node=row)
                    db.commit()
                    return
                except Exception as exc:
                    if row.attempt <= retries:
                        row.status = "retrying"
                        row.error_data = {"message": str(exc), "type": type(exc).__name__}
                        _event(db, execution, "node_retrying", f"Retrying {definition.get('label')}: {exc}", node=row, level="warning")
                        db.commit()
                        await asyncio.sleep(min(2 ** row.attempt, 10))
                        row.attempt += 1
                        row.status = "running"
                        db.commit()
                        continue
                    raise

        final = next((row.output_data for row in reversed(rows) if row.status == "completed"), {}) or {}
        execution.status = "completed"
        execution.current_node_id = None
        execution.completed_at = utcnow()
        execution.final_output = final
        bot = db.query(AutomationBot).filter(AutomationBot.id == execution.bot_id).first()
        if bot:
            bot.last_run_at = utcnow()
            bot.next_run_at = next_run_at(bot.schedule_config or {}, timezone_name=bot.timezone) if bot.status == "active" else None
        _event(db, execution, "execution_completed", "Workflow execution completed")
        if (execution.settings_snapshot or {}).get("in_app_notifications", True):
            db.add(AutomationNotification(
                user_id=execution.user_id,
                execution_id=execution.id,
                title="Bot run completed",
                message=f"{bot.name if bot else 'Automation'} completed successfully.",
            ))
        db.commit()
    except Exception as exc:
        db.rollback()
        execution = db.query(AutomationExecution).filter(AutomationExecution.id == execution_id).first()
        if execution:
            execution.status = "failed"
            execution.error_summary = str(exc)
            execution.completed_at = utcnow()
            if execution.current_node_id:
                row = db.query(AutomationNodeExecution).filter(
                    AutomationNodeExecution.execution_id == execution.id,
                    AutomationNodeExecution.node_id == execution.current_node_id,
                ).first()
                if row:
                    row.status = "failed"
                    row.completed_at = utcnow()
                    row.error_data = {"message": str(exc), "type": type(exc).__name__}
            _event(db, execution, "execution_failed", f"Workflow failed: {exc}", level="error")
            db.commit()
        logger.exception("Automation execution %s failed", execution_id)
    finally:
        db.close()


def run_execution_sync(execution_id: int) -> None:
    asyncio.run(run_execution(execution_id))


async def run_due_bots() -> int:
    db = SessionLocal()
    queued: List[int] = []
    try:
        now = utcnow()
        bots = db.query(AutomationBot).filter(
            AutomationBot.status == "active",
            AutomationBot.next_run_at.isnot(None),
            AutomationBot.next_run_at <= now,
        ).all()
        for bot in bots:
            slot = bot.next_run_at.isoformat() if bot.next_run_at else now.isoformat()
            key = f"schedule:{bot.id}:{slot}"
            existing = db.query(AutomationExecution).filter(
                AutomationExecution.user_id == bot.user_id,
                AutomationExecution.idempotency_key == key,
            ).first()
            if existing:
                continue
            execution = create_execution(db, bot, trigger_type="scheduled", idempotency_key=key)
            bot.next_run_at = next_run_at(bot.schedule_config or {}, now, bot.timezone)
            db.commit()
            queued.append(execution.id)
    finally:
        db.close()
    for execution_id in queued:
        await run_execution(execution_id)
    return len(queued)


def make_idempotency_key(prefix: str = "manual") -> str:
    return f"{prefix}:{uuid4().hex}"
