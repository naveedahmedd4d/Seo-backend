import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.gsc_page_metric import GscPageMetric
from app.models.gsc_property import GscProperty
from app.models.gsc_query_metric import GscQueryMetric
from app.models.published_post import PublishedPost
from app.models.seo_action_item import SeoActionItem
from app.models.workflow_state import WorkflowState

logger = logging.getLogger(__name__)

EXPECTED_CTR = {
    1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.06,
    6: 0.05, 7: 0.04, 8: 0.03, 9: 0.025, 10: 0.02,
}

URGENCY = {
    "low_ctr": 2.0,
    "zero_clicks": 1.8,
    "striking_distance": 1.5,
    "content_decay": 2.5,
    "keyword_gap": 1.2,
    "cannibalization": 1.4,
}

FIXABILITY = {
    "low_ctr": 1.0,
    "zero_clicks": 1.0,
    "striking_distance": 0.7,
    "content_decay": 0.4,
    "keyword_gap": 0.7,
    "cannibalization": 0.5,
}


def expected_ctr(position: float) -> float:
    rank = round(position)
    if rank > 10:
        return 0.01
    rank = max(1, rank)
    return EXPECTED_CTR.get(rank, 0.01)


def normalize_page_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    path = parsed.path or "/"
    path = path.rstrip("/") or "/"
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host:
        return f"{host}{path}"
    return path


def compute_impact_score(action_type: str, impressions: int) -> float:
    urgency = URGENCY.get(action_type, 1.0)
    fixability = FIXABILITY.get(action_type, 0.5)
    raw = impressions * urgency * fixability
    return min(100.0, round(raw / 50.0, 1))


def _severity_for(action_type: str, impressions: int) -> str:
    if action_type in ("low_ctr", "content_decay") and impressions >= 1000:
        return "critical"
    if action_type in ("low_ctr", "striking_distance", "content_decay", "cannibalization"):
        return "warning"
    return "info"


def _suggestion(action_type: str, snapshot: dict[str, Any]) -> str:
    pos = snapshot.get("position")
    ctr = snapshot.get("ctr")
    exp = snapshot.get("expected_ctr")
    imp = snapshot.get("impressions")
    query = snapshot.get("query") or ""
    prior = snapshot.get("prior_position")

    if action_type == "low_ctr":
        ctr_pct = f"{(ctr or 0) * 100:.1f}%"
        exp_pct = f"{(exp or 0) * 100:.0f}%"
        return (
            f"Rewrite title and meta description for \"{query}\". "
            f"CTR {ctr_pct} is well below expected ~{exp_pct} at position {pos:.1f}."
        )
    if action_type == "zero_clicks":
        return (
            f"Page gets {imp:,} impressions at position {pos:.1f} but zero clicks. "
            "Test a new title and meta description aligned with search intent."
        )
    if action_type == "striking_distance":
        return (
            f"\"{query}\" ranks at position {pos:.1f} with {imp:,} impressions. "
            "Add internal links, expand content, and improve H1 to reach page 1."
        )
    if action_type == "content_decay":
        return (
            f"Position dropped from {prior:.1f} to {pos:.1f} for \"{query}\". "
            "Refresh content, check indexing, and update facts or news hooks."
        )
    if action_type == "keyword_gap":
        return (
            f"Page ranks for \"{query}\" but it is not in the focus keyword strategy. "
            "Update focus keyword or add a dedicated section targeting this query."
        )
    if action_type == "cannibalization":
        pages = snapshot.get("competing_pages") or []
        return (
            f"Multiple pages compete for \"{query}\": {', '.join(pages[:3])}. "
            "Consolidate content, differentiate intent, or strengthen internal linking."
        )
    return "Review and optimize this page for better search performance."


class PageAggregate:
    __slots__ = ("clicks", "impressions", "ctr", "position")

    def __init__(self) -> None:
        self.clicks = 0
        self.impressions = 0
        self.ctr = 0.0
        self.position = 0.0


def _aggregate_pages(metrics: list[GscPageMetric]) -> dict[str, PageAggregate]:
    by_page: dict[str, PageAggregate] = {}
    for m in metrics:
        agg = by_page.setdefault(m.page, PageAggregate())
        agg.clicks += m.clicks
        agg.impressions += m.impressions
        agg.position += m.position * m.impressions
    for page, agg in by_page.items():
        if agg.impressions > 0:
            agg.ctr = agg.clicks / agg.impressions
            agg.position = agg.position / agg.impressions
        by_page[page] = agg
    return by_page


def _aggregate_query_page(metrics: list[GscQueryMetric]) -> dict[tuple[str, str], PageAggregate]:
    by_key: dict[tuple[str, str], PageAggregate] = {}
    for m in metrics:
        key = (m.query, m.page)
        agg = by_key.setdefault(key, PageAggregate())
        agg.clicks += m.clicks
        agg.impressions += m.impressions
        agg.position += m.position * m.impressions
    for key, agg in by_key.items():
        if agg.impressions > 0:
            agg.ctr = agg.clicks / agg.impressions
            agg.position = agg.position / agg.impressions
        by_key[key] = agg
    return by_key


def _build_published_post_map(db: Session, user_id: int) -> dict[str, tuple[int, int]]:
    """Map normalized page URL -> (published_post_id, workflow_id)."""
    posts = db.query(PublishedPost).filter(PublishedPost.user_id == user_id).all()
    result: dict[str, tuple[int, int]] = {}
    for post in posts:
        if post.wp_link:
            norm = normalize_page_url(post.wp_link)
            result[norm] = (post.id, post.workflow_id)
    return result


def _workflow_keywords(db: Session, workflow_id: int) -> set[str]:
    wf = db.query(WorkflowState).filter(WorkflowState.id == workflow_id).first()
    if not wf or not wf.state_data:
        return set()
    data = wf.state_data if isinstance(wf.state_data, dict) else {}
    keywords: set[str] = set()
    fk = data.get("focus_keyword")
    if fk:
        keywords.add(str(fk).lower())
    for article in [data, data.get("parent") or {}, *(data.get("children") or [])]:
        if not isinstance(article, dict):
            continue
        if article.get("focus_keyword"):
            keywords.add(str(article["focus_keyword"]).lower())
    strategy = data.get("keyword_strategy") or {}
    for key in ("primary", "secondary", "lsi", "long_tail"):
        val = strategy.get(key)
        if isinstance(val, str):
            keywords.add(val.lower())
        elif isinstance(val, list):
            keywords.update(str(v).lower() for v in val)
    return keywords


def _date_cutoffs() -> tuple[date, date, date, date]:
    end = date.today() - timedelta(days=3)
    current_start = end - timedelta(days=27)
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=27)
    return current_start, end, prior_start, prior_end


def detect_issues(
    current_query: dict[tuple[str, str], PageAggregate],
    prior_query: dict[tuple[str, str], PageAggregate],
    current_pages: dict[str, PageAggregate],
    prior_pages: dict[str, PageAggregate],
    published_map: dict[str, tuple[int, int]],
    workflow_keywords_fn,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen_cannibal: set[str] = set()

    for (query, page), agg in current_query.items():
        if agg.impressions < 20:
            continue

        exp = expected_ctr(agg.position)
        linked = published_map.get(normalize_page_url(page))

        # Low CTR
        if agg.impressions >= 100 and agg.position <= 10 and agg.ctr < exp * 0.5:
            snapshot = {
                "position": round(agg.position, 1),
                "ctr": round(agg.ctr, 4),
                "expected_ctr": round(exp, 4),
                "impressions": agg.impressions,
                "clicks": agg.clicks,
                "query": query,
            }
            issues.append({
                "action_type": "low_ctr",
                "page": page,
                "query": query,
                "severity": _severity_for("low_ctr", agg.impressions),
                "metric_snapshot": snapshot,
                "impact_score": compute_impact_score("low_ctr", agg.impressions),
                "linked_published_post_id": linked[0] if linked else None,
                "linked_workflow_id": linked[1] if linked else None,
            })

        # Zero clicks
        if agg.impressions >= 500 and agg.clicks == 0:
            snapshot = {
                "position": round(agg.position, 1),
                "ctr": 0.0,
                "impressions": agg.impressions,
                "clicks": 0,
                "query": query,
            }
            issues.append({
                "action_type": "zero_clicks",
                "page": page,
                "query": query,
                "severity": _severity_for("zero_clicks", agg.impressions),
                "metric_snapshot": snapshot,
                "impact_score": compute_impact_score("zero_clicks", agg.impressions),
                "linked_published_post_id": linked[0] if linked else None,
                "linked_workflow_id": linked[1] if linked else None,
            })

        # Striking distance
        if 8 <= agg.position <= 20 and agg.impressions >= 50:
            snapshot = {
                "position": round(agg.position, 1),
                "ctr": round(agg.ctr, 4),
                "impressions": agg.impressions,
                "clicks": agg.clicks,
                "query": query,
            }
            issues.append({
                "action_type": "striking_distance",
                "page": page,
                "query": query,
                "severity": _severity_for("striking_distance", agg.impressions),
                "metric_snapshot": snapshot,
                "impact_score": compute_impact_score("striking_distance", agg.impressions),
                "linked_published_post_id": linked[0] if linked else None,
                "linked_workflow_id": linked[1] if linked else None,
            })

        # Content decay
        prior = prior_query.get((query, page))
        if prior and prior.impressions >= 50:
            if agg.position - prior.position >= 3:
                snapshot = {
                    "position": round(agg.position, 1),
                    "prior_position": round(prior.position, 1),
                    "impressions": agg.impressions,
                    "query": query,
                }
                issues.append({
                    "action_type": "content_decay",
                    "page": page,
                    "query": query,
                    "severity": _severity_for("content_decay", agg.impressions),
                    "metric_snapshot": snapshot,
                    "impact_score": compute_impact_score("content_decay", agg.impressions),
                    "linked_published_post_id": linked[0] if linked else None,
                    "linked_workflow_id": linked[1] if linked else None,
                })

        # Keyword gap
        if linked and agg.impressions >= 100:
            kws = workflow_keywords_fn(linked[1])
            if kws and query.lower() not in kws and not any(q in query.lower() for q in kws):
                snapshot = {
                    "position": round(agg.position, 1),
                    "impressions": agg.impressions,
                    "query": query,
                }
                issues.append({
                    "action_type": "keyword_gap",
                    "page": page,
                    "query": query,
                    "severity": "info",
                    "metric_snapshot": snapshot,
                    "impact_score": compute_impact_score("keyword_gap", agg.impressions),
                    "linked_published_post_id": linked[0],
                    "linked_workflow_id": linked[1],
                })

    # Cannibalization
    query_pages: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (query, page), agg in current_query.items():
        if agg.impressions >= 20:
            query_pages[query].append((page, agg.impressions))

    for query, pages in query_pages.items():
        if len(pages) < 2 or query in seen_cannibal:
            continue
        seen_cannibal.add(query)
        total_imp = sum(p[1] for p in pages)
        snapshot = {
            "query": query,
            "competing_pages": [p[0] for p in sorted(pages, key=lambda x: -x[1])],
            "impressions": total_imp,
        }
        issues.append({
            "action_type": "cannibalization",
            "page": pages[0][0],
            "query": query,
            "severity": "warning",
            "metric_snapshot": snapshot,
            "impact_score": compute_impact_score("cannibalization", total_imp),
            "linked_published_post_id": None,
            "linked_workflow_id": None,
        })

    for issue in issues:
        issue["suggestion"] = _suggestion(issue["action_type"], issue["metric_snapshot"])

    return sorted(issues, key=lambda i: i["impact_score"], reverse=True)


def run_analysis_for_property(db: Session, user_id: int, property_id: int) -> int:
    current_start, current_end, prior_start, prior_end = _date_cutoffs()

    current_page_metrics = (
        db.query(GscPageMetric)
        .filter(
            GscPageMetric.property_id == property_id,
            GscPageMetric.date >= current_start,
            GscPageMetric.date <= current_end,
        )
        .all()
    )
    prior_page_metrics = (
        db.query(GscPageMetric)
        .filter(
            GscPageMetric.property_id == property_id,
            GscPageMetric.date >= prior_start,
            GscPageMetric.date <= prior_end,
        )
        .all()
    )
    current_query_metrics = (
        db.query(GscQueryMetric)
        .filter(
            GscQueryMetric.property_id == property_id,
            GscQueryMetric.date >= current_start,
            GscQueryMetric.date <= current_end,
        )
        .all()
    )
    prior_query_metrics = (
        db.query(GscQueryMetric)
        .filter(
            GscQueryMetric.property_id == property_id,
            GscQueryMetric.date >= prior_start,
            GscQueryMetric.date <= prior_end,
        )
        .all()
    )

    published_map = _build_published_post_map(db, user_id)

    def kw_fn(wid: int) -> set[str]:
        return _workflow_keywords(db, wid)

    issues = detect_issues(
        _aggregate_query_page(current_query_metrics),
        _aggregate_query_page(prior_query_metrics),
        _aggregate_pages(current_page_metrics),
        _aggregate_pages(prior_page_metrics),
        published_map,
        kw_fn,
    )

    preserved_statuses = {"approved", "dismissed", "snoozed"}
    existing = (
        db.query(SeoActionItem)
        .filter(SeoActionItem.property_id == property_id)
        .all()
    )
    existing_keys = {
        (e.action_type, e.page or "", e.query or ""): e
        for e in existing
        if e.status in preserved_statuses
    }

    db.query(SeoActionItem).filter(
        SeoActionItem.property_id == property_id,
        SeoActionItem.status == "pending_review",
    ).delete(synchronize_session=False)

    created = 0
    for issue in issues:
        key = (issue["action_type"], issue.get("page") or "", issue.get("query") or "")
        if key in existing_keys:
            continue
        db.add(
            SeoActionItem(
                property_id=property_id,
                user_id=user_id,
                action_type=issue["action_type"],
                severity=issue["severity"],
                page=issue.get("page"),
                query=issue.get("query"),
                metric_snapshot=issue["metric_snapshot"],
                suggestion=issue["suggestion"],
                impact_score=issue["impact_score"],
                status="pending_review",
                linked_published_post_id=issue.get("linked_published_post_id"),
                linked_workflow_id=issue.get("linked_workflow_id"),
            )
        )
        created += 1

    return created


def build_overview(db: Session, user_id: int, property_id: int) -> dict[str, Any]:
    current_start, current_end, prior_start, prior_end = _date_cutoffs()

    def _sum_metrics(model, start: date, end: date) -> dict[str, float]:
        rows = (
            db.query(
                func.sum(model.clicks).label("clicks"),
                func.sum(model.impressions).label("impressions"),
                func.avg(model.position).label("position"),
            )
            .filter(
                model.property_id == property_id,
                model.date >= start,
                model.date <= end,
            )
            .first()
        )
        clicks = int(rows.clicks or 0)
        impressions = int(rows.impressions or 0)
        ctr = clicks / impressions if impressions else 0.0
        position = float(rows.position or 0.0)
        return {"clicks": clicks, "impressions": impressions, "ctr": ctr, "position": position}

    current = _sum_metrics(GscPageMetric, current_start, current_end)
    prior = _sum_metrics(GscPageMetric, prior_start, prior_end)

    def _pct_change(cur: float, prev: float) -> float:
        if prev == 0:
            return 100.0 if cur > 0 else 0.0
        return round((cur - prev) / prev * 100, 1)

    changes = {
        "clicks_pct": _pct_change(current["clicks"], prior["clicks"]),
        "impressions_pct": _pct_change(current["impressions"], prior["impressions"]),
        "ctr_pp": round((current["ctr"] - prior["ctr"]) * 100, 1),
        "position_delta": round(current["position"] - prior["position"], 1),
    }

    # Weekly trend (4 buckets)
    trend = []
    for week_idx in range(4):
        w_start = current_start + timedelta(days=week_idx * 7)
        w_end = min(w_start + timedelta(days=6), current_end)
        week_clicks = (
            db.query(func.sum(GscPageMetric.clicks))
            .filter(
                GscPageMetric.property_id == property_id,
                GscPageMetric.date >= w_start,
                GscPageMetric.date <= w_end,
            )
            .scalar()
        ) or 0
        trend.append({"week": f"W{week_idx + 1}", "clicks": int(week_clicks)})

    # Issue breakdown
    breakdown_rows = (
        db.query(SeoActionItem.action_type, func.count(SeoActionItem.id))
        .filter(
            SeoActionItem.property_id == property_id,
            SeoActionItem.status == "pending_review",
        )
        .group_by(SeoActionItem.action_type)
        .all()
    )
    issue_breakdown = {row[0]: row[1] for row in breakdown_rows}
    for key in ("low_ctr", "striking_distance", "content_decay", "zero_clicks", "cannibalization", "keyword_gap"):
        issue_breakdown.setdefault(key, 0)

    open_count = (
        db.query(func.count(SeoActionItem.id))
        .filter(
            SeoActionItem.property_id == property_id,
            SeoActionItem.status == "pending_review",
        )
        .scalar()
    ) or 0

    # Top pages
    top_rows = (
        db.query(
            GscPageMetric.page,
            func.sum(GscPageMetric.clicks).label("clicks"),
            func.avg(GscPageMetric.position).label("position"),
        )
        .filter(
            GscPageMetric.property_id == property_id,
            GscPageMetric.date >= current_start,
            GscPageMetric.date <= current_end,
        )
        .group_by(GscPageMetric.page)
        .order_by(func.sum(GscPageMetric.clicks).desc())
        .limit(5)
        .all()
    )
    top_pages = [
        {"page": r.page, "clicks": int(r.clicks or 0), "position": round(float(r.position or 0), 1)}
        for r in top_rows
    ]

    prop = db.query(GscProperty).filter(GscProperty.id == property_id).first()

    return {
        "summary": {**current, "changes": changes},
        "trend": trend,
        "issue_breakdown": issue_breakdown,
        "top_pages": top_pages,
        "open_action_count": open_count,
        "last_synced_at": prop.last_synced_at.isoformat() if prop and prop.last_synced_at else None,
        "data_through": current_end.isoformat(),
    }
