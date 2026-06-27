from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.trending_keywords import TrendingKeywords

SOURCE_MANUAL = "manual"
SOURCE_TRENDING = "trending"


def _has_completed_blog_workflow(k: TrendingKeywords) -> bool:
    return (
        k.bio_blog_workflow_id is not None
        or k.net_worth_blog_workflow_id is not None
        or k.blog_workflow_id is not None
    )


def _keyword_to_dict(k: TrendingKeywords) -> Dict[str, Any]:
    keyword_data = k.data if isinstance(k.data, dict) else {}
    bio_id = k.bio_blog_workflow_id if k.bio_blog_workflow_id is not None else k.blog_workflow_id
    return {
        "id": k.id,
        "keyword": k.keyword,
        "data": k.data,
        "category": k.category,
        "fetched_at": k.fetched_at.isoformat() if k.fetched_at else None,
        "bio_blog_workflow_id": bio_id,
        "net_worth_blog_workflow_id": k.net_worth_blog_workflow_id,
        "source": k.source,
        "value": keyword_data.get("value") if isinstance(keyword_data, dict) else None,
        "timestamp": keyword_data.get("timestamp") if isinstance(keyword_data, dict) else None,
    }


def create_manual_keywords(
    db: Session,
    keywords: List[str],
    category: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], int, int]:
    """Insert manual keywords. Returns (created rows, added count, skipped duplicates)."""
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

    if not normalized:
        return [], 0, 0

    existing = {
        row.keyword.lower()
        for row in db.query(TrendingKeywords.keyword)
        .filter(TrendingKeywords.source == SOURCE_MANUAL)
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
            source=SOURCE_MANUAL,
        )
        db.add(row)
        created.append(row)
        existing.add(text.lower())

    if created:
        db.commit()
        for row in created:
            db.refresh(row)

    return [_keyword_to_dict(row) for row in created], len(created), skipped


def get_manual_keywords_from_db(
    db: Session,
    completed_only: bool = False,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get manual keywords with no time window."""
    query = db.query(TrendingKeywords).filter(
        TrendingKeywords.source == SOURCE_MANUAL
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
    return [_keyword_to_dict(k) for k in rows]
