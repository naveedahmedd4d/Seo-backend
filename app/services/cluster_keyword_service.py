"""Cluster keywords: main (pillar) + nested sub-keywords."""

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.trending_keywords import TrendingKeywords

SOURCE_CLUSTER = "cluster"


def _has_completed_blog_workflow(k: TrendingKeywords) -> bool:
    return (
        k.bio_blog_workflow_id is not None
        or k.net_worth_blog_workflow_id is not None
        or k.blog_workflow_id is not None
    )


def _keyword_to_dict(k: TrendingKeywords, include_subs: bool = False) -> Dict[str, Any]:
    keyword_data = k.data if isinstance(k.data, dict) else {}
    bio_id = k.bio_blog_workflow_id if k.bio_blog_workflow_id is not None else k.blog_workflow_id
    result: Dict[str, Any] = {
        "id": k.id,
        "keyword": k.keyword,
        "data": k.data,
        "category": k.category,
        "fetched_at": k.fetched_at.isoformat() if k.fetched_at else None,
        "bio_blog_workflow_id": bio_id,
        "net_worth_blog_workflow_id": k.net_worth_blog_workflow_id,
        "source": k.source,
        "parent_id": k.parent_id,
        "kd": k.kd,
        "value": keyword_data.get("value") if isinstance(keyword_data, dict) else None,
        "timestamp": keyword_data.get("timestamp") if isinstance(keyword_data, dict) else None,
    }
    if include_subs:
        subs = sorted(
            [s for s in (k.subs or []) if s.source == SOURCE_CLUSTER],
            key=lambda s: s.fetched_at or s.id,
            reverse=True,
        )
        result["subs"] = [_keyword_to_dict(s, include_subs=False) for s in subs]
    return result


def _normalize_keywords(keywords: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for raw in keywords:
        text = raw.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def create_cluster_mains(
    db: Session,
    keywords: List[str],
    category: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Insert cluster main keywords (parent_id NULL). Returns (created, added, skipped)."""
    normalized = _normalize_keywords(keywords)
    if not normalized:
        return [], 0, 0

    existing = {
        row.keyword.lower()
        for row in db.query(TrendingKeywords.keyword)
        .filter(
            TrendingKeywords.source == SOURCE_CLUSTER,
            TrendingKeywords.parent_id.is_(None),
        )
        .all()
    }

    created: List[TrendingKeywords] = []
    skipped = 0
    for text in normalized:
        if text.lower() in existing:
            skipped += 1
            continue
        row = TrendingKeywords(
            keyword=text,
            data={},
            category=category,
            source=SOURCE_CLUSTER,
            parent_id=None,
        )
        db.add(row)
        created.append(row)
        existing.add(text.lower())

    if created:
        db.commit()
        for row in created:
            db.refresh(row)

    return [_keyword_to_dict(row, include_subs=True) for row in created], len(created), skipped


def create_cluster_subs(
    db: Session,
    parent_id: int,
    keywords: List[str],
) -> Tuple[Optional[TrendingKeywords], List[Dict[str, Any]], int, int, Optional[str]]:
    """
    Insert sub-keywords under a cluster main.
    Returns (parent, created_dicts, added, skipped, error_code).
    error_code: None | 'not_found' | 'not_main'
    """
    parent = db.query(TrendingKeywords).filter(TrendingKeywords.id == parent_id).first()
    if not parent:
        return None, [], 0, 0, "not_found"
    if parent.source != SOURCE_CLUSTER or parent.parent_id is not None:
        return None, [], 0, 0, "not_main"

    normalized = _normalize_keywords(keywords)
    if not normalized:
        return parent, [], 0, 0, None

    existing = {
        row.keyword.lower()
        for row in db.query(TrendingKeywords.keyword)
        .filter(
            TrendingKeywords.source == SOURCE_CLUSTER,
            TrendingKeywords.parent_id == parent_id,
        )
        .all()
    }

    created: List[TrendingKeywords] = []
    skipped = 0
    for text in normalized:
        if text.lower() in existing:
            skipped += 1
            continue
        row = TrendingKeywords(
            keyword=text,
            data={},
            category=parent.category,
            source=SOURCE_CLUSTER,
            parent_id=parent_id,
        )
        db.add(row)
        created.append(row)
        existing.add(text.lower())

    if created:
        db.commit()
        for row in created:
            db.refresh(row)

    return parent, [_keyword_to_dict(row) for row in created], len(created), skipped, None


def get_cluster_keywords_from_db(
    db: Session,
    completed_only: bool = False,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get cluster main keywords with nested subs. No time window."""
    query = db.query(TrendingKeywords).filter(
        TrendingKeywords.source == SOURCE_CLUSTER,
        TrendingKeywords.parent_id.is_(None),
    )
    if completed_only:
        query = query.filter(
            (TrendingKeywords.bio_blog_workflow_id.isnot(None))
            | (TrendingKeywords.net_worth_blog_workflow_id.isnot(None))
            | (TrendingKeywords.blog_workflow_id.isnot(None))
        )
    else:
        query = query.filter(
            TrendingKeywords.bio_blog_workflow_id.is_(None),
            TrendingKeywords.net_worth_blog_workflow_id.is_(None),
            TrendingKeywords.blog_workflow_id.is_(None),
        )
    if category:
        query = query.filter(TrendingKeywords.category == category)
    rows = query.order_by(TrendingKeywords.fetched_at.desc()).all()
    return [_keyword_to_dict(k, include_subs=True) for k in rows]
