from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.trending_keywords import TrendingKeywords
from app.services.serpapi_service import (
    ALLOWED_TREND_HOURS,
    fetch_trending_keywords,
    get_trending_keywords_from_db,
    get_used_trending_keywords_from_db,
)
from app.services.manual_keyword_service import (
    create_manual_keywords,
    get_manual_keywords_from_db,
    SOURCE_MANUAL,
    SOURCE_TRENDING,
)
from app.services.cluster_keyword_service import (
    SOURCE_CLUSTER,
    create_cluster_mains,
    create_cluster_subs,
    get_cluster_keywords_from_db,
)
from app.services.github_trending_service import (
    GITHUB_LANGUAGES,
    SOURCE_GITHUB,
    fetch_github_trending,
    get_github_trending_from_db,
)
from app.services.openrouter_service import categorize_keyword, generate_github_repo_ideas
from app.services.mangools_service import (
    MangoolsError,
    SOFT_BATCH_CAP,
    cooldown_before_import,
    fetch_mangools_quota,
    import_keyword_metrics,
)
from app.services.llm_settings_service import resolve_api_key, resolve_api_key_optional
from app.schemas.trending import (
    TrendingKeywordsResponse,
    TrendingKeywordResponse,
    AddManualKeywordsRequest,
    AddClusterSubsRequest,
    SetBlogWorkflowRequest,
    EstimateKdRequest,
    EstimateKdResponse,
    MangoolsLimitsResponse,
    SyncCoverageRequest,
    SyncCoverageResponse,
    GenerateGithubIdeasRequest,
    GenerateGithubIdeasResponse,
)
from app.services.keyword_coverage_service import (
    STATUS_EXISTING,
    STATUS_NEW,
    coverage_map_for_site,
    coverage_to_dict,
    get_site_for_user,
    sync_coverage_for_site,
)

router = APIRouter()
SOURCE_REDDIT = "reddit"


def _coerce_int(value) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) or None
    if isinstance(value, str):
        digits = value.replace(",", "").strip()
        if digits.isdigit():
            return int(digits) or None
    return None


def _pick_search_volume(data: dict, fallback=None) -> Optional[int]:
    """Best available numeric search volume: Mangools SV → SerpAPI value/search_volume."""
    if isinstance(data, dict):
        for key in ("mangools_sv", "value", "search_volume"):
            got = _coerce_int(data.get(key))
            if got:
                return got
    return _coerce_int(fallback)


def _build_keyword_dict(k: dict, coverage_by_id: Optional[dict] = None) -> dict:
    keyword_data = k.get("data", {}) if isinstance(k.get("data"), dict) else {}
    coverage_by_id = coverage_by_id or {}
    subs_raw = k.get("subs")
    subs = None
    if isinstance(subs_raw, list):
        subs = []
        for s in subs_raw:
            if not isinstance(s, dict):
                continue
            sub_id = s.get("id")
            sub_cov = coverage_to_dict(coverage_by_id.get(sub_id)) if sub_id else None
            subs.append(
                TrendingKeywordResponse(
                    id=sub_id,
                    keyword=s.get("keyword", ""),
                    value=_pick_search_volume(
                        s.get("data") if isinstance(s.get("data"), dict) else s,
                        s.get("value"),
                    ),
                    increase_percentage=_coerce_int(
                        (s.get("data") or {}).get("increase_percentage")
                        if isinstance(s.get("data"), dict)
                        else s.get("increase_percentage")
                    ),
                    timestamp=s.get("timestamp"),
                    fetched_at=s.get("fetched_at"),
                    category=s.get("category"),
                    kd=s.get("kd"),
                    bio_blog_workflow_id=s.get("bio_blog_workflow_id"),
                    net_worth_blog_workflow_id=s.get("net_worth_blog_workflow_id"),
                    blog_workflow_id=s.get("blog_workflow_id"),
                    source=s.get("source"),
                    parent_id=s.get("parent_id"),
                    coverage=sub_cov,
                )
            )
    kid = k.get("id")
    cov = coverage_to_dict(coverage_by_id.get(kid)) if kid else None
    keyword_response = TrendingKeywordResponse(
        keyword=k.get("keyword", ""),
        value=_pick_search_volume(keyword_data, k.get("value")),
        increase_percentage=_coerce_int(
            keyword_data.get("increase_percentage")
            if isinstance(keyword_data, dict)
            else k.get("increase_percentage")
        ),
        timestamp=keyword_data.get("timestamp") if isinstance(keyword_data, dict) else k.get("timestamp"),
        fetched_at=k.get("fetched_at"),
        category=k.get("category"),
        kd=k.get("kd"),
        bio_blog_workflow_id=k.get("bio_blog_workflow_id"),
        net_worth_blog_workflow_id=k.get("net_worth_blog_workflow_id"),
        blog_workflow_id=k.get("blog_workflow_id"),
        source=k.get("source"),
        parent_id=k.get("parent_id"),
        subs=subs,
        coverage=cov,
    )
    keyword_dict = keyword_response.model_dump()
    keyword_dict["id"] = kid
    keyword_dict["kd"] = k.get("kd")
    keyword_dict["bio_blog_workflow_id"] = k.get("bio_blog_workflow_id")
    keyword_dict["net_worth_blog_workflow_id"] = k.get("net_worth_blog_workflow_id")
    keyword_dict["blog_workflow_id"] = k.get("blog_workflow_id")
    keyword_dict["source"] = k.get("source")
    keyword_dict["parent_id"] = k.get("parent_id")
    keyword_dict["coverage"] = cov
    if k.get("source") == SOURCE_GITHUB and isinstance(keyword_data, dict):
        keyword_dict["repo"] = {
            "description": keyword_data.get("description"),
            "url": keyword_data.get("url"),
            "language": keyword_data.get("language"),
            "stars_today": keyword_data.get("increase_percentage"),
            "forks": keyword_data.get("forks"),
            "topics": keyword_data.get("topics"),
            "suggested_titles": keyword_data.get("suggested_titles"),
            "suggested_angles": keyword_data.get("suggested_angles"),
            "suggested_keywords": keyword_data.get("suggested_keywords"),
        }
    return keyword_dict


def _collect_keyword_ids(keywords: list) -> list:
    ids: list = []
    for k in keywords:
        kid = k.get("id") if isinstance(k, dict) else None
        if kid is not None:
            ids.append(kid)
        subs = k.get("subs") if isinstance(k, dict) else None
        if isinstance(subs, list):
            for s in subs:
                if isinstance(s, dict) and s.get("id") is not None:
                    ids.append(s["id"])
    return ids


def _filter_by_coverage_status(keywords: list, coverage_status: str) -> list:
    """Keep keywords (and cluster mains with matching subs) by coverage status."""
    filtered = []
    for k in keywords:
        cov = k.get("coverage") if isinstance(k, dict) else None
        status_val = cov.get("status") if isinstance(cov, dict) else None
        subs = k.get("subs") if isinstance(k, dict) else None
        if isinstance(subs, list):
            matched_subs = [
                s
                for s in subs
                if isinstance(s, dict)
                and isinstance(s.get("coverage"), dict)
                and s["coverage"].get("status") == coverage_status
            ]
            if status_val == coverage_status:
                row = dict(k)
                if matched_subs:
                    row["subs"] = matched_subs
                filtered.append(row)
            elif matched_subs:
                row = dict(k)
                row["subs"] = matched_subs
                filtered.append(row)
            continue
        if status_val == coverage_status:
            filtered.append(k)
    return filtered


def _attach_coverage_and_filter(
    db: Session,
    user: User,
    keywords: list,
    wordpress_site_id: Optional[int],
    coverage_status: Optional[str],
) -> list:
    if wordpress_site_id is None:
        return [_build_keyword_dict(k) for k in keywords]
    get_site_for_user(db, user, wordpress_site_id)
    ids = _collect_keyword_ids(keywords)
    cov_map = coverage_map_for_site(db, wordpress_site_id, ids)
    built = [_build_keyword_dict(k, cov_map) for k in keywords]
    if coverage_status in (STATUS_EXISTING, STATUS_NEW):
        return _filter_by_coverage_status(built, coverage_status)
    return built


@router.get("/keywords", response_model=TrendingKeywordsResponse)
async def get_trending_keywords(
    refresh: bool = False,
    hours: int = 4,
    category: Optional[str] = None,
    completed_only: bool = False,
    source: str = SOURCE_TRENDING,
    language: str = "all",
    wordpress_site_id: Optional[int] = None,
    coverage_status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get trending, manual, cluster, or GitHub keywords, optionally filtered by category."""
    if source not in (SOURCE_TRENDING, SOURCE_MANUAL, SOURCE_CLUSTER, SOURCE_GITHUB):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source must be 'trending', 'manual', 'cluster', or 'github'",
        )
    if coverage_status is not None and coverage_status not in (STATUS_EXISTING, STATUS_NEW):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="coverage_status must be 'existing' or 'new'",
        )
    if coverage_status and wordpress_site_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="wordpress_site_id is required when filtering by coverage_status",
        )
    if source == SOURCE_TRENDING and not completed_only and hours not in ALLOWED_TREND_HOURS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"hours must be one of {', '.join(str(h) for h in ALLOWED_TREND_HOURS)}",
        )
    try:
        if source == SOURCE_MANUAL:
            cached_data = get_manual_keywords_from_db(db, completed_only, category, user_id=current_user.id)
            keywords = _attach_coverage_and_filter(
                db, current_user, cached_data, wordpress_site_id, coverage_status
            )
            return TrendingKeywordsResponse(keywords=keywords, count=len(keywords))

        if source == SOURCE_CLUSTER:
            cached_data = get_cluster_keywords_from_db(db, completed_only, category, user_id=current_user.id)
            keywords = _attach_coverage_and_filter(
                db, current_user, cached_data, wordpress_site_id, coverage_status
            )
            return TrendingKeywordsResponse(keywords=keywords, count=len(keywords))

        if source == SOURCE_GITHUB:
            cached_data = get_github_trending_from_db(db, language, user_id=current_user.id)
            keywords = _attach_coverage_and_filter(
                db, current_user, cached_data, wordpress_site_id, coverage_status
            )
            return TrendingKeywordsResponse(keywords=keywords, count=len(keywords))

        if completed_only:
            cached_data = get_used_trending_keywords_from_db(db, category, user_id=current_user.id)
            keywords = _attach_coverage_and_filter(
                db, current_user, cached_data, wordpress_site_id, coverage_status
            )
            return TrendingKeywordsResponse(keywords=keywords, count=len(keywords))

        if refresh:
            serp_key = resolve_api_key(db, current_user, "serpapi")
            groq_fb = resolve_api_key_optional(db, current_user, "trending_fallback_groq") or resolve_api_key_optional(
                db, current_user, "groq"
            )
            keywords_data = await fetch_trending_keywords(
                db, hours, api_key=serp_key, groq_fallback_key=groq_fb, user_id=current_user.id
            )
            cached_data = get_trending_keywords_from_db(db, hours, category, user_id=current_user.id)
            keyword_id_map = {k.get("keyword"): k.get("id") for k in cached_data if k.get("keyword")}

            raw_keywords = []
            for k in keywords_data:
                keyword_dict = dict(k) if isinstance(k, dict) else {}
                keyword_dict["id"] = keyword_id_map.get(k.get("keyword", ""))
                raw_keywords.append(keyword_dict)
            keywords = _attach_coverage_and_filter(
                db, current_user, raw_keywords, wordpress_site_id, coverage_status
            )
        else:
            cached_data = get_trending_keywords_from_db(db, hours, category, user_id=current_user.id)
            keywords = _attach_coverage_and_filter(
                db, current_user, cached_data, wordpress_site_id, coverage_status
            )

        return TrendingKeywordsResponse(
            keywords=keywords,
            count=len(keywords)
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching trending keywords: {str(e)}"
        )


@router.post("/keywords/coverage/sync", response_model=SyncCoverageResponse)
async def sync_keyword_coverage(
    body: SyncCoverageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Compare keywords for a source against a WordPress site's published content."""
    try:
        result = await sync_coverage_for_site(
            db,
            current_user,
            body.wordpress_site_id,
            body.source,
        )
        return SyncCoverageResponse(
            existing=result["existing"],
            new=result["new"],
            total=result["total"],
            compared_at=result["compared_at"],
            wordpress_site_id=result["wordpress_site_id"],
            source=result["source"],
            message=(
                f"Compared {result['total']} keywords: "
                f"{result['existing']} existing, {result['new']} new."
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error syncing keyword coverage: {str(e)}",
        )


@router.post("/keywords/manual", response_model=TrendingKeywordsResponse)
async def add_manual_keywords(
    body: AddManualKeywordsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add one or more manual keywords (one per list item)."""
    try:
        created, added, skipped = create_manual_keywords(db, body.keywords, body.category, user_id=current_user.id)
        if added == 0 and skipped > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="All keywords already exist in manual list",
            )
        keywords = [_build_keyword_dict(k) for k in created]
        return TrendingKeywordsResponse(keywords=keywords, count=added)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error adding manual keywords: {str(e)}",
        )


@router.post("/keywords/cluster", response_model=TrendingKeywordsResponse)
async def add_cluster_main_keywords(
    body: AddManualKeywordsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add one or more cluster main (pillar) keywords."""
    try:
        created, added, skipped = create_cluster_mains(db, body.keywords, body.category, user_id=current_user.id)
        if added == 0 and skipped > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="All keywords already exist as cluster mains",
            )
        keywords = [_build_keyword_dict(k) for k in created]
        return TrendingKeywordsResponse(keywords=keywords, count=added)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error adding cluster keywords: {str(e)}",
        )


@router.post("/keywords/{keyword_id}/subs", response_model=TrendingKeywordsResponse)
async def add_cluster_sub_keywords(
    keyword_id: int,
    body: AddClusterSubsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add sub-keywords under a cluster main keyword."""
    try:
        _parent, created, added, skipped, error = create_cluster_subs(
            db, keyword_id, body.keywords, user_id=current_user.id
        )
        if error == "not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Keyword not found",
            )
        if error == "not_main":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Subs can only be added under a cluster main keyword",
            )
        if added == 0 and skipped > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="All sub-keywords already exist under this main",
            )
        keywords = [_build_keyword_dict(k) for k in created]
        return TrendingKeywordsResponse(keywords=keywords, count=added)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error adding cluster sub-keywords: {str(e)}",
        )


@router.get("/keywords/mangools-limits", response_model=MangoolsLimitsResponse)
async def get_mangools_limits(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return Mangools KWFinder keyword-lookup remaining quota for the UI badge."""
    try:
        token = resolve_api_key(db, current_user, "mangools")
        quota = await fetch_mangools_quota(token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except MangoolsError as e:
        code = e.status_code or status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=code, detail=str(e))

    reset_hint = ""
    if quota.reset_seconds is not None and quota.reset_seconds > 0:
        mins = max(1, quota.reset_seconds // 60)
        reset_hint = f" Resets in ~{mins} min." if mins < 120 else f" Resets in ~{mins // 60} h."

    return MangoolsLimitsResponse(
        remaining=quota.remaining,
        limit=quota.limit,
        reset_seconds=quota.reset_seconds,
        short_period_max=quota.short_period_max,
        upgraded_account=quota.upgraded_account,
        message=f"Mangools KD lookups: {quota.remaining}/{quota.limit}.{reset_hint}",
    )


@router.post("/keywords/estimate-kd", response_model=EstimateKdResponse)
async def estimate_keyword_difficulty(
    body: EstimateKdRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Score Mangools KD for selected keywords only (free-tier safe).
    Requires keyword_ids. Caps batch by remaining Mangools keyword lookups.
    """
    if body.source not in (SOURCE_TRENDING, SOURCE_MANUAL, SOURCE_CLUSTER, SOURCE_REDDIT):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source must be 'trending', 'manual', 'cluster', or 'reddit'",
        )

    if not body.keyword_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select one or more keywords first, then click Estimate KD.",
        )

    try:
        mangools_token = resolve_api_key(db, current_user, "mangools")
        quota = await fetch_mangools_quota(mangools_token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except MangoolsError as e:
        code = e.status_code or status.HTTP_503_SERVICE_UNAVAILABLE
        if code in (401, 429, 503):
            raise HTTPException(status_code=code, detail=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )

    remaining_before = quota.remaining
    if remaining_before <= 0:
        return EstimateKdResponse(
            scored=0,
            skipped=0,
            remaining_before=0,
            remaining_after=0,
            limit=quota.limit,
            reset_seconds=quota.reset_seconds,
            message=(
                f"No Mangools keyword lookups remaining ({quota.remaining}/{quota.limit}). "
                "Wait for the 24h quota reset, then select keywords and try again."
            ),
            keywords=[],
        )

    query = db.query(TrendingKeywords).filter(
        TrendingKeywords.source == body.source,
        TrendingKeywords.id.in_(body.keyword_ids),
        TrendingKeywords.user_id == current_user.id,
    )
    if not body.force:
        query = query.filter(TrendingKeywords.kd.is_(None))

    candidates = query.order_by(TrendingKeywords.fetched_at.desc()).all()
    if not candidates:
        return EstimateKdResponse(
            scored=0,
            skipped=0,
            remaining_before=remaining_before,
            remaining_after=remaining_before,
            limit=quota.limit,
            reset_seconds=quota.reset_seconds,
            message=(
                f"No selected keywords need KD "
                f"(already scored or invalid). Lookups left: {remaining_before}/{quota.limit}."
            ),
            keywords=[],
        )

    batch_size = min(len(candidates), remaining_before, SOFT_BATCH_CAP)
    batch = candidates[:batch_size]
    skipped = len(candidates) - len(batch)

    # Reduce chance of short-period 429 after the limits GET.
    await cooldown_before_import()

    try:
        metrics = await import_keyword_metrics(
            [row.keyword for row in batch], api_token=mangools_token
        )
    except MangoolsError as e:
        code = e.status_code or status.HTTP_502_BAD_GATEWAY
        if code in (401, 429, 503):
            raise HTTPException(status_code=code, detail=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )

    by_kw = {m["keyword"].lower(): m for m in metrics if m.get("keyword")}
    scored_rows: list[TrendingKeywords] = []

    for row in batch:
        metric = by_kw.get(row.keyword.lower())
        if not metric:
            continue
        kd_val = metric.get("kd")
        if kd_val is None:
            continue

        row.kd = int(kd_val)
        mangools_sv = metric.get("mangools_sv")
        if mangools_sv is not None:
            data = dict(row.data) if isinstance(row.data, dict) else {}
            data["mangools_sv"] = mangools_sv
            if not data.get("value"):
                data["value"] = mangools_sv
            row.data = data
        scored_rows.append(row)

    if scored_rows:
        db.commit()
        for row in scored_rows:
            db.refresh(row)

    remaining_after = max(0, remaining_before - len(batch))
    try:
        # Don't burn short-period quota with a third request; estimate is fine.
        pass
    except Exception:
        pass

    message = (
        f"Scored KD for {len(scored_rows)} of {len(batch)} selected keyword"
        f"{'' if len(batch) == 1 else 's'}."
    )
    if skipped > 0:
        message += f" Skipped {skipped} (quota cap {remaining_before}/{quota.limit})."
    if any(m.get("kd") is None for m in metrics):
        message += " Some keywords had no cached KD from Mangools yet."
    message += f" Lookups left (est.): {remaining_after}/{quota.limit}."

    return EstimateKdResponse(
        scored=len(scored_rows),
        skipped=skipped,
        remaining_before=remaining_before,
        remaining_after=remaining_after,
        limit=quota.limit,
        reset_seconds=quota.reset_seconds,
        message=message,
        keywords=[
            _build_keyword_dict(
                {
                    "id": r.id,
                    "keyword": r.keyword,
                    "data": r.data if isinstance(r.data, dict) else {},
                    "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
                    "category": r.category,
                    "kd": r.kd,
                    "bio_blog_workflow_id": r.bio_blog_workflow_id,
                    "net_worth_blog_workflow_id": r.net_worth_blog_workflow_id,
                    "blog_workflow_id": r.blog_workflow_id,
                    "source": r.source,
                    "parent_id": r.parent_id,
                }
            )
            for r in scored_rows
        ],
    )


@router.post("/keywords/fetch")
async def fetch_keywords(
    hours: int = 4,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch trending keywords from SerpAPI and return from database (last N hours)"""
    if hours not in ALLOWED_TREND_HOURS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"hours must be one of {', '.join(str(h) for h in ALLOWED_TREND_HOURS)}",
        )
    try:
        serp_key = resolve_api_key(db, current_user, "serpapi")
        groq_fb = resolve_api_key_optional(db, current_user, "trending_fallback_groq") or resolve_api_key_optional(
            db, current_user, "groq"
        )
        keywords_data = await fetch_trending_keywords(
            db, hours, api_key=serp_key, groq_fallback_key=groq_fb, user_id=current_user.id
        )
        keywords = [_build_keyword_dict(k) for k in keywords_data]
        return TrendingKeywordsResponse(
            keywords=keywords,
            count=len(keywords)
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching trending keywords: {str(e)}"
        )


@router.post("/keywords/github/fetch", response_model=TrendingKeywordsResponse)
async def fetch_github_keywords(
    language: str = "all",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Scrape github.com/trending (daily view only) and return cached repos."""
    valid_languages = {code for code, _ in GITHUB_LANGUAGES}
    if language not in valid_languages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"language must be one of {sorted(valid_languages)}",
        )
    try:
        keywords_data = await fetch_github_trending(db, language, user_id=current_user.id)
        keywords = [_build_keyword_dict(k) for k in keywords_data]
        return TrendingKeywordsResponse(keywords=keywords, count=len(keywords))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching GitHub trending: {str(e)}",
        )


@router.post("/keywords/github/generate-ideas", response_model=GenerateGithubIdeasResponse)
async def generate_github_ideas(
    body: GenerateGithubIdeasRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate LLM-derived blog titles, angles, and keywords for selected GitHub repos."""
    if not body.keyword_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select one or more repos first.",
        )
    rows = (
        db.query(TrendingKeywords)
        .filter(
            TrendingKeywords.source == SOURCE_GITHUB,
            TrendingKeywords.id.in_(body.keyword_ids),
            TrendingKeywords.user_id == current_user.id,
        )
        .all()
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No matching repos found.")

    processed = 0
    failed = 0
    updated_rows: list[TrendingKeywords] = []
    for row in rows:
        data = dict(row.data) if isinstance(row.data, dict) else {}
        try:
            ideas = await generate_github_repo_ideas(
                row.keyword, data.get("description"), data.get("language"), data.get("topics"),
            )
            data["suggested_titles"] = ideas["titles"]
            data["suggested_angles"] = ideas["angles"]
            data["suggested_keywords"] = ideas["keywords"]
            row.data = data
            processed += 1
            updated_rows.append(row)
        except Exception:
            failed += 1

    if updated_rows:
        db.commit()
        for r in updated_rows:
            db.refresh(r)

    return GenerateGithubIdeasResponse(
        processed=processed,
        failed=failed,
        message=(
            f"Generated ideas for {processed} repo{'' if processed == 1 else 's'}."
            + (f" {failed} failed." if failed else "")
        ),
        keywords=[
            _build_keyword_dict({
                "id": r.id,
                "keyword": r.keyword,
                "data": r.data if isinstance(r.data, dict) else {},
                "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
                "category": r.category,
                "kd": r.kd,
                "bio_blog_workflow_id": r.bio_blog_workflow_id,
                "net_worth_blog_workflow_id": r.net_worth_blog_workflow_id,
                "blog_workflow_id": r.blog_workflow_id,
                "source": r.source,
            })
            for r in updated_rows
        ],
    )


@router.patch("/keywords/{keyword_id}/blog-workflow")
async def set_keyword_blog_workflow(
    keyword_id: int,
    body: SetBlogWorkflowRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set the blog workflow ID for a trending keyword (mark as completed)."""
    keyword = db.query(TrendingKeywords).filter(
        TrendingKeywords.id == keyword_id,
        TrendingKeywords.user_id == current_user.id,
    ).first()
    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keyword not found",
        )
    if body.content_type == "biography":
        keyword.bio_blog_workflow_id = body.workflow_id
    elif body.content_type in ("cluster_hub", "news_timeline", "news_explainer", "news_opinion"):
        keyword.blog_workflow_id = body.workflow_id
    else:
        keyword.net_worth_blog_workflow_id = body.workflow_id
    db.commit()
    db.refresh(keyword)
    return {
        "message": "Blog workflow linked to keyword",
        "keyword_id": keyword_id,
        "workflow_id": body.workflow_id,
        "content_type": body.content_type,
    }


@router.delete("/keywords/{keyword_id}")
async def delete_keyword(
    keyword_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a trending keyword"""
    try:
        keyword = db.query(TrendingKeywords).filter(
            TrendingKeywords.id == keyword_id,
            TrendingKeywords.user_id == current_user.id,
        ).first()
        if not keyword:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Keyword not found"
            )
        db.delete(keyword)
        db.commit()
        return {"message": "Keyword deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting keyword: {str(e)}"
        )


@router.post("/keywords/categorize")
async def categorize_keywords(
    recategorize: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Categorize trending keywords (not manual) using LLM"""
    try:
        if recategorize:
            keywords = db.query(TrendingKeywords).filter(
                TrendingKeywords.source == SOURCE_TRENDING,
                TrendingKeywords.user_id == current_user.id,
            ).all()
        else:
            keywords = db.query(TrendingKeywords).filter(
                TrendingKeywords.category.is_(None),
                TrendingKeywords.source == SOURCE_TRENDING,
                TrendingKeywords.user_id == current_user.id,
            ).all()

        if not keywords:
            return {
                "message": "No keywords to categorize",
                "total": 0,
                "categorized": 0,
                "failed": 0
            }

        batch_size = 15
        categorized_count = 0
        failed_count = 0

        for i in range(0, len(keywords), batch_size):
            batch = keywords[i:i + batch_size]

            for keyword_obj in batch:
                try:
                    category = await categorize_keyword(keyword_obj.keyword)
                    if category:
                        keyword_obj.category = category
                        categorized_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    import traceback
                    error_details = traceback.format_exc()
                    print(f"Error categorizing keyword '{keyword_obj.keyword}': {str(e)}")
                    print(f"Full traceback: {error_details}")
                    failed_count += 1

            db.commit()

        categorized_keywords = []
        for k in keywords:
            if k.category:
                keyword_data = k.data if isinstance(k.data, dict) else {}
                keyword_dict = _build_keyword_dict({
                    "id": k.id,
                    "keyword": k.keyword,
                    "data": keyword_data,
                    "fetched_at": k.fetched_at.isoformat() if k.fetched_at else None,
                    "category": k.category,
                    "bio_blog_workflow_id": k.bio_blog_workflow_id,
                    "net_worth_blog_workflow_id": k.net_worth_blog_workflow_id,
                    "blog_workflow_id": k.blog_workflow_id,
                    "source": k.source,
                })
                categorized_keywords.append(keyword_dict)

        return {
            "message": f"Categorized {categorized_count} keywords successfully",
            "total": len(keywords),
            "categorized": categorized_count,
            "failed": failed_count,
            "keywords": categorized_keywords
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error categorizing keywords: {str(e)}"
        )


@router.get("/keywords/history")
async def get_keyword_history(
    hours: int = 24,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get keyword history"""
    try:
        cached_data = get_trending_keywords_from_db(db, hours, user_id=current_user.id)
        return {
            "keywords": cached_data,
            "count": len(cached_data)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching keyword history: {str(e)}"
        )
