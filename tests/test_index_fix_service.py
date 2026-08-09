"""Unit tests for index fix planner."""

from app.models.seo_action_item import SeoActionItem
from app.services.index_fix_service import plan_fixes


def _action(**kwargs) -> SeoActionItem:
    item = SeoActionItem(
        property_id=1,
        user_id=1,
        action_type="not_indexed",
        severity="warning",
        page="https://example.com/post/",
        impact_score=50.0,
        status="pending_review",
    )
    for key, value in kwargs.items():
        setattr(item, key, value)
    return item


def test_plan_fixes_clear_noindex():
    action = _action(
        metric_snapshot={
            "indexing_state": "BLOCKED_BY_META_TAG",
            "live_signals": {"has_noindex": True},
            "diagnosis": {"root_cause": "noindex"},
        }
    )
    fixes = plan_fixes(action, {"success": True, "status": "publish", "meta": {}})
    assert any(f.fix_type == "clear_noindex" for f in fixes)


def test_plan_fixes_manual_only_for_robots_blocked():
    action = _action(
        metric_snapshot={
            "diagnosis": {"root_cause": "robots_blocked"},
            "live_signals": {},
        }
    )
    assert plan_fixes(action) == []


def test_plan_fixes_publish_draft_and_meta():
    action = _action(
        metric_snapshot={
            "diagnosis": {"root_cause": "thin_content"},
            "live_signals": {"meta_description": "short"},
        },
        linked_workflow_id=99,
    )
    wp_post = {"success": True, "status": "draft", "meta": {}, "title": "Test Post"}
    fixes = plan_fixes(action, wp_post)
    types = {f.fix_type for f in fixes}
    assert "publish_post" in types
    assert "update_meta_description" in types
    assert "expand_content" in types
