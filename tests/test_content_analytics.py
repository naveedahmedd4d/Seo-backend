from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.content_analytics import GaPageMetric, GaProperty, SocialAccountMetricSnapshot, SocialPostMetricSnapshot
from app.models.gsc_page_metric import GscPageMetric
from app.models.gsc_property import GscProperty
from app.models.published_post import PublishedPost
from app.models.social_post_history import SocialPostHistory
from app.models.social_post_publication import SocialPostPublication
from app.models.social_account import SocialAccount
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.models.workflow_state import WorkflowState, WorkflowStatus, WorkflowType
from app.services.content_analytics_service import build_blog_rows, build_overview, build_social_rows, resolve_range


engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)


def _seed(db):
    user = User(email="analytics@example.com", password_hash="x")
    other = User(email="other@example.com", password_hash="x")
    db.add_all([user, other]); db.flush()
    site = WordPressSite(user_id=user.id, name="Example", url="https://example.com", username="editor", api_key="encrypted")
    db.add(site); db.flush()
    workflow = WorkflowState(
        user_id=user.id, workflow_type=WorkflowType.BLOG, status=WorkflowStatus.COMPLETED,
        state_data={"topic": "Analytics guide", "focus_keyword": "content analytics", "category": "Technology & AI"},
    )
    db.add(workflow); db.flush()
    published = PublishedPost(
        user_id=user.id, workflow_id=workflow.id, wordpress_site_id=site.id, blog_key="parent",
        wp_post_id=10, wp_link="https://example.com/analytics-guide/", canonical_url="https://example.com/analytics-guide/",
        wp_status="publish", title="Analytics guide", published_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    draft = PublishedPost(
        user_id=user.id, workflow_id=workflow.id, wordpress_site_id=site.id, blog_key="child_0",
        wp_post_id=11, wp_link="https://example.com/draft/", wp_status="draft", title="Draft",
        published_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    db.add_all([published, draft]); db.flush()
    gsc = GscProperty(user_id=user.id, site_url="sc-domain:example.com", wordpress_site_id=site.id)
    ga = GaProperty(user_id=user.id, property_id="123", wordpress_site_id=site.id)
    db.add_all([gsc, ga]); db.flush()
    db.add_all([
        GscPageMetric(property_id=gsc.id, date=date(2026, 9, 10), page="https://www.example.com/analytics-guide", clicks=4, impressions=40, ctr=.1, position=2),
        GscPageMetric(property_id=gsc.id, date=date(2026, 9, 11), page="https://example.com/analytics-guide/", clicks=6, impressions=60, ctr=.1, position=4),
        GscPageMetric(property_id=gsc.id, date=date(2026, 9, 11), page="https://example.com/unmanaged", clicks=99, impressions=999, ctr=.1, position=1),
        GaPageMetric(property_id=ga.id, date=date(2026, 9, 10), page_path="/analytics-guide/", views=120, sessions=80, active_users=70, organic_sessions=50),
    ])
    history = SocialPostHistory(user_id=user.id, query="analytics", headline="Analytics post", related_published_post_id=published.id)
    db.add(history); db.flush()
    success = SocialPostPublication(user_id=user.id, history_id=history.id, platform="x", status="success", external_id="1", published_at=datetime(2026, 9, 12, tzinfo=timezone.utc))
    failure = SocialPostPublication(user_id=user.id, history_id=history.id, platform="facebook", status="failed", published_at=datetime(2026, 9, 12, tzinfo=timezone.utc))
    db.add_all([success, failure]); db.flush()
    db.add(SocialPostMetricSnapshot(
        user_id=user.id, publication_id=success.id, snapshot_date=date(2026, 9, 13),
        impressions=1000, reach=800, reactions=40, comments=5, reposts=4, link_clicks=20,
    ))
    account = SocialAccount(user_id=user.id, platform="x", account_label="Analytics")
    db.add(account); db.flush()
    db.add_all([
        SocialAccountMetricSnapshot(user_id=user.id, social_account_id=account.id, snapshot_date=date(2026, 8, 31), followers_count=100),
        SocialAccountMetricSnapshot(user_id=user.id, social_account_id=account.id, snapshot_date=date(2026, 9, 20), followers_count=112),
    ])
    db.commit()
    return user, site


def test_published_only_blog_metrics_and_weighted_position():
    Base.metadata.create_all(engine)
    db = Session()
    try:
        user, site = _seed(db)
        period = resolve_range("custom", date(2026, 9, 1), date(2026, 9, 30), "UTC")
        rows = build_blog_rows(db, user.id, period, site.id)
        assert len(rows) == 1
        assert rows[0]["title"] == "Analytics guide"
        assert rows[0]["clicks"]["value"] == 10
        assert rows[0]["impressions"]["value"] == 100
        assert rows[0]["average_position"]["value"] == 3.2
        assert rows[0]["views"]["value"] == 120
        assert rows[0]["organic_traffic"]["value"] == 50
    finally:
        db.close(); Base.metadata.drop_all(engine)


def test_social_only_counts_successful_publications():
    Base.metadata.create_all(engine)
    db = Session()
    try:
        user, _ = _seed(db)
        period = resolve_range("custom", date(2026, 9, 1), date(2026, 9, 30), "UTC")
        rows = build_social_rows(db, user.id, period)
        assert len(rows) == 1
        assert rows[0]["platform"] == "x"
        assert rows[0]["engagement_rate"]["value"] == 0.0862
    finally:
        db.close(); Base.metadata.drop_all(engine)


def test_overview_timeseries_excludes_unmanaged_pages():
    Base.metadata.create_all(engine)
    db = Session()
    try:
        user, site = _seed(db)
        period = resolve_range("custom", date(2026, 9, 1), date(2026, 9, 30), "UTC")
        overview = build_overview(db, user.id, period, site.id)
        assert overview["blog_summary"]["published"]["total"] == 1
        assert sum(point["clicks"] for point in overview["timeseries"]) == 10
        assert sum(point["impressions"] for point in overview["timeseries"]) == 100
        assert overview["social_summary"]["followers"]["value"] == 112
        assert overview["social_summary"]["follower_growth"]["value"] == 12
    finally:
        db.close(); Base.metadata.drop_all(engine)
