"""Pure validation, deduplication, clustering, and scoring for Reddit Trends."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse, urlunparse

from app.services.openrouter_service import generate_content, reset_llm_context, set_llm_context

_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_URL_RE = re.compile(r"https?://\S+", re.I)
_DELETED_MARKERS = {"[deleted]", "[removed]", "deleted", "removed"}

TOPIC_RULES = (
    ("Generative AI", ("generative ai", "image generator", "video generator")),
    ("AI Agents", ("ai agent", "agentic", "multi-agent", "multi agent")),
    ("Large Language Models", ("llm", "large language model", "language model")),
    ("Machine Learning", ("machine learning", "deep learning", "neural network")),
    ("OpenAI", ("openai", "chatgpt", "gpt-")),
    ("Anthropic", ("anthropic", "claude")),
    ("Google Gemini", ("gemini", "google ai")),
    ("NVIDIA AI", ("nvidia", "cuda")),
    ("Software Development", ("programming", "developer", "software", "github", "coding")),
    ("Technology News", ("technology", "tech news", "launch", "released", "announced")),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return ensure_aware(value)
    if isinstance(value, (int, float)) and value > 0:
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return parse_datetime(float(text))
    text = text.replace("Z", "+00:00")
    try:
        return ensure_aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = _URL_RE.sub(" ", text)
    text = _NON_WORD_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def canonicalize_reddit_url(value: str) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    if value.startswith("/"):
        value = f"https://www.reddit.com{value}"
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in {"reddit.com", "www.reddit.com", "old.reddit.com"}:
        return None
    path = re.sub(r"/+", "/", parsed.path or "/")
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunparse(("https", "www.reddit.com", path.rstrip("/") + "/", "", "", ""))


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value or 0)
        return max(0.0, number) if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def validate_post_item(item: Dict[str, Any], *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    kind = str(item.get("kind") or item.get("type") or "post").lower()
    nsfw = item.get("over_18", item.get("is_nsfw", item.get("isNsfw", False)))
    removed = item.get(
        "is_deleted_or_removed",
        item.get("is_removed", item.get("removed_by_category", False)),
    )
    nsfw_flag = str(nsfw).strip().lower() in {"1", "true", "yes"}
    removed_flag = str(removed).strip().lower() not in {"", "0", "false", "none", "null"}
    if kind not in {"post", "t3"} or nsfw_flag or removed_flag:
        return None
    post_id = str(item.get("id") or item.get("post_id") or item.get("postId") or "").strip()
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or item.get("selftext") or item.get("text") or "").strip()
    author = str(item.get("author") or item.get("author_name") or "").strip()
    if not post_id or not title or title.lower() in _DELETED_MARKERS:
        return None
    if body.lower() in _DELETED_MARKERS or author.lower() in _DELETED_MARKERS:
        return None
    canonical_url = canonicalize_reddit_url(
        str(
            item.get("canonical_url")
            or item.get("post_url")
            or item.get("url")
            or item.get("permalink")
            or ""
        )
    )
    if not canonical_url:
        return None
    subreddit = str(
        item.get("subreddit") or item.get("subreddit_name") or item.get("community_name") or ""
    ).strip().removeprefix("r/")
    created = parse_datetime(
        item.get("created_utc")
        or item.get("created_at")
        or item.get("createdAt")
        or item.get("createdTimestamp")
    )
    if not subreddit or not created:
        return None
    retrieved = parse_datetime(item.get("retrieved_at")) or now or utcnow()
    score = _to_int(item.get("score"))
    comments = _to_int(item.get("num_comments", item.get("comments")))
    engagement = _to_int(item.get("engagement_total")) or score + comments
    age_hours = max(0.25, (ensure_aware(retrieved) - ensure_aware(created)).total_seconds() / 3600)
    score_per_hour = _to_float(item.get("score_per_hour", item.get("scorePerHour")))
    comments_per_hour = _to_float(
        item.get("comments_per_hour", item.get("commentsPerHour"))
    )
    if "score_per_hour" not in item and "scorePerHour" not in item:
        score_per_hour = score / age_hours
    if "comments_per_hour" not in item and "commentsPerHour" not in item:
        comments_per_hour = comments / age_hours
    return {
        "reddit_post_id": post_id,
        "canonical_url": canonical_url,
        "title": title[:1000],
        "normalized_title": normalize_title(title),
        "body_excerpt": body[:2000] or None,
        "author": author[:255] or None,
        "subreddit": subreddit[:255],
        "score": score,
        "comments": comments,
        "score_per_hour": score_per_hour,
        "comments_per_hour": comments_per_hour,
        "upvote_ratio": min(
            1.0, _to_float(item.get("upvote_ratio", item.get("upvoteRatio")))
        ),
        "engagement_total": engagement,
        "created_utc": created,
        "retrieved_at": retrieved,
        "raw_data": item,
    }


def deduplicate_posts(posts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    seen_urls: set[str] = set()
    for post in posts:
        post_id = post["reddit_post_id"]
        url = post["canonical_url"]
        if post_id in by_id or url in seen_urls:
            existing = by_id.get(post_id)
            if existing and post.get("retrieved_at") > existing.get("retrieved_at"):
                by_id[post_id] = post
            continue
        by_id[post_id] = post
        seen_urls.add(url)
    return list(by_id.values())


def _similar(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    left = a["normalized_title"]
    right = b["normalized_title"]
    if left == right:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return False
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / max(1, len(union))
    sequence = SequenceMatcher(None, left, right).ratio()
    return jaccard >= 0.58 or sequence >= 0.80


def cluster_posts(posts: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    parent = list(range(len(posts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(posts)):
        for j in range(i + 1, len(posts)):
            if _similar(posts[i], posts[j]):
                union(i, j)

    groups: Dict[int, List[Dict[str, Any]]] = {}
    for idx, post in enumerate(posts):
        groups.setdefault(find(idx), []).append(post)
    return list(groups.values())


def topic_type_for(text: str) -> str:
    value = normalize_title(text)
    for label, needles in TOPIC_RULES:
        if any(needle in value for needle in needles):
            return label
    return "Technology News"


def _representative(cluster: List[Dict[str, Any]]) -> Dict[str, Any]:
    return max(
        cluster,
        key=lambda p: (p.get("engagement_total", 0), p.get("score_per_hour", 0.0)),
    )


async def enrich_clusters_with_llm(
    clusters: List[List[Dict[str, Any]]],
    credentials: Any,
) -> List[Dict[str, Any]]:
    """Let the configured model merge ambiguous groups and label topics.

    Deterministic clusters remain the fallback if the configured model is absent,
    unavailable, or returns malformed JSON.
    """
    base = [
        {
            "members": cluster,
            "topic": _representative(cluster)["title"],
            "summary": (_representative(cluster).get("body_excerpt") or "")[:280] or None,
        }
        for cluster in clusters
    ]
    if not credentials or not base:
        return base

    prompt_rows = [
        {
            "id": idx,
            "title": row["topic"],
            "subreddits": sorted({p["subreddit"] for p in row["members"]}),
        }
        for idx, row in enumerate(base[:60])
    ]
    prompt = (
        "Group only entries that discuss the same underlying news or technology topic. "
        "Do not merge broad category matches. Return JSON only as "
        '{"groups":[{"ids":[0,1],"topic":"concise SEO keyword",'
        '"summary":"one sentence","category":"Technology & AI",'
        '"topic_type":"AI Agents"}]}. Every id must appear exactly once. Entries:\n'
        + json.dumps(prompt_rows, ensure_ascii=False)
    )
    token = set_llm_context(credentials)
    try:
        raw = await generate_content(prompt, max_tokens=1800, temperature=0.1, timeout_seconds=90)
    except Exception:
        return base
    finally:
        reset_llm_context(token)

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        payload = json.loads(cleaned.strip())
        output: List[Dict[str, Any]] = []
        used: set[int] = set()
        for group in payload.get("groups") or []:
            ids = [int(i) for i in group.get("ids") or [] if 0 <= int(i) < len(base)]
            ids = [i for i in ids if i not in used]
            if not ids:
                continue
            used.update(ids)
            members = [post for i in ids for post in base[i]["members"]]
            output.append({
                "members": members,
                "topic": str(group.get("topic") or _representative(members)["title"]).strip()[:500],
                "summary": str(group.get("summary") or "").strip()[:1000] or None,
                "category": str(group.get("category") or "Technology & AI").strip()[:255],
                "topic_type": str(group.get("topic_type") or topic_type_for(group.get("topic") or "")).strip()[:100],
            })
        for idx, row in enumerate(base):
            if idx not in used:
                output.append(row)
        return output or base
    except (TypeError, ValueError, json.JSONDecodeError):
        return base


def aggregate_clusters(clusters: List[Dict[str, Any]], enabled_subreddits: int) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for cluster in clusters:
        members = cluster["members"]
        representative = _representative(members)
        score = sum(int(p.get("score") or 0) for p in members)
        comments = sum(int(p.get("comments") or 0) for p in members)
        score_velocity = sum(float(p.get("score_per_hour") or 0) for p in members)
        comment_velocity = sum(float(p.get("comments_per_hour") or 0) for p in members)
        weight_total = sum(max(1, int(p.get("score") or 0)) for p in members)
        ratio = sum(float(p.get("upvote_ratio") or 0) * max(1, int(p.get("score") or 0)) for p in members) / max(1, weight_total)
        subreddits = sorted({str(p.get("subreddit") or "") for p in members if p.get("subreddit")})
        topic = str(cluster.get("topic") or representative["title"]).strip()
        fingerprint_source = " ".join(sorted(set(normalize_title(topic).split()))) or representative["normalized_title"]
        rows.append({
            "members": members,
            "representative": representative,
            "fingerprint": hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
            "topic": topic,
            "summary": cluster.get("summary") or (representative.get("body_excerpt") or "")[:280] or None,
            "category": cluster.get("category") or "Technology & AI",
            "topic_type": cluster.get("topic_type") or topic_type_for(topic),
            "primary_subreddit": representative["subreddit"],
            "subreddit_mentions": len(subreddits),
            "post_score": score,
            "comments": comments,
            "score_per_hour": score_velocity,
            "comments_per_hour": comment_velocity,
            "upvote_ratio": min(1.0, max(0.0, ratio)),
            "engagement_total": score + comments,
            "latest_post_at": max(p["created_utc"] for p in members),
        })

    def p95(values: List[float]) -> float:
        values = sorted(v for v in values if v > 0)
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, math.ceil(len(values) * 0.95) - 1))
        return values[index]

    score_p95 = p95([row["score_per_hour"] for row in rows])
    comments_p95 = p95([row["comments_per_hour"] for row in rows])
    cross_denominator = max(1, enabled_subreddits - 1)
    for row in rows:
        row["normalized_score_per_hour"] = min(100.0, row["score_per_hour"] / score_p95 * 100.0) if score_p95 else 0.0
        row["normalized_comments_per_hour"] = min(100.0, row["comments_per_hour"] / comments_p95 * 100.0) if comments_p95 else 0.0
        row["normalized_upvote_ratio"] = row["upvote_ratio"] * 100.0
        row["normalized_cross_subreddit"] = min(
            100.0,
            max(0, row["subreddit_mentions"] - 1) / cross_denominator * 100.0,
        )
        row["trend_score"] = round(
            row["normalized_score_per_hour"] * 0.40
            + row["normalized_comments_per_hour"] * 0.35
            + row["normalized_upvote_ratio"] * 0.15
            + row["normalized_cross_subreddit"] * 0.10,
            2,
        )
    rows.sort(key=lambda row: row["trend_score"], reverse=True)
    return rows, {
        "score_per_hour_p95": score_p95,
        "comments_per_hour_p95": comments_p95,
        "enabled_subreddits": enabled_subreddits,
        "formula": "score_velocity*.40 + comment_velocity*.35 + upvote_ratio*.15 + cross_subreddit*.10",
    }
