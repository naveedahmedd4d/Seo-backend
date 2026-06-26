"""Tests for the social platform registry."""

import json
from pathlib import Path

import pytest

from app.services.social_platform_registry import (
    REGISTRY_PATH,
    clear_registry_cache,
    get_social_platforms,
)


@pytest.fixture(autouse=True)
def reset_cache():
    clear_registry_cache()
    yield
    clear_registry_cache()


def test_registry_loads_without_error():
    platforms = get_social_platforms()
    assert len(platforms) >= 16


def test_registry_sorted_by_sort_order():
    platforms = get_social_platforms()
    sort_keys = [(p.sort_order, p.name.lower()) for p in platforms]
    assert sort_keys == sorted(sort_keys)


def test_filter_by_category():
    social = get_social_platforms(category="social")
    assert len(social) > 0
    assert all(p.category == "social" for p in social)


def test_filter_by_content_type():
    image_platforms = get_social_platforms(content_type="image_card")
    assert len(image_platforms) > 0
    assert all("image_card" in p.content_types for p in image_platforms)


def test_invalid_entry_skipped(tmp_path, monkeypatch):
    bad_registry = tmp_path / "social_platforms.json"
    bad_registry.write_text(
        json.dumps(
            [
                {
                    "id": "valid",
                    "name": "Valid Platform",
                    "category": "social",
                    "auth_type": "api_key",
                    "content_types": ["link"],
                    "status": "coming_soon",
                    "post_api_available": True,
                    "connect_enabled": False,
                    "sort_order": 1,
                    "description": "Valid entry",
                },
                {"id": "broken"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.social_platform_registry.REGISTRY_PATH",
        bad_registry,
    )
    clear_registry_cache()
    platforms = get_social_platforms()
    assert len(platforms) == 1
    assert platforms[0].id == "valid"


def test_production_registry_has_expected_ids():
    platforms = get_social_platforms()
    ids = {p.id for p in platforms}
    assert "reddit" in ids
    assert "devto" in ids
    assert "pinterest" in ids
    assert "linkedin" in ids
    assert "facebook" in ids
    assert "instagram" in ids


def test_linkedin_facebook_instagram_are_active():
    platforms = {p.id: p for p in get_social_platforms()}
    for platform_id in ("linkedin", "facebook", "instagram"):
        p = platforms[platform_id]
        assert p.status == "active"
        assert p.connect_enabled is True


def test_registry_file_exists():
    assert REGISTRY_PATH.is_file()
