"""Tests for WordPress category mapping and resolution."""

import pytest

from app.models.wordpress_site import WordPressSite
from app.services.wordpress_category_service import (
    APP_TO_WP_CATEGORY,
    PILLAR_CONTENT_WP_CATEGORY,
    ensure_wp_category,
    resolve_wp_category_id,
    resolve_wp_category_ids_for_app_category,
    resolve_wp_category_ids_for_cluster_keyword,
    wp_category_name_for_app_category,
    _category_id_cache,
)
from app.models.trending_keywords import TrendingKeywords


def test_wp_category_name_for_app_category():
    assert wp_category_name_for_app_category("Sports") == "Sport"
    assert wp_category_name_for_app_category("Celebrities & Public Figures") == "Celebrities"
    assert wp_category_name_for_app_category("News & Politics") == "Politicians"
    assert wp_category_name_for_app_category("Technology & AI") is None
    assert wp_category_name_for_app_category("Pillar Content") == "Pillar Content"
    assert wp_category_name_for_app_category(None) is None
    assert wp_category_name_for_app_category("") is None


def test_app_to_wp_mapping_has_expected_keys():
    assert "Sports" in APP_TO_WP_CATEGORY
    assert APP_TO_WP_CATEGORY["Sports"] == "Sport"


@pytest.mark.asyncio
async def test_resolve_wp_category_id_matches_exact_name(monkeypatch):
    _category_id_cache.clear()

    site = WordPressSite(
        id=1,
        user_id=1,
        name="Test",
        url="https://example.com",
        api_key="encrypted",
        username="admin",
    )

    class FakeResponse:
        status_code = 200

        def json(self):
            return [
                {"id": 3, "name": "Politicians"},
                {"id": 7, "name": "Sport"},
            ]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, auth=None, params=None):
            assert "categories" in url
            assert params["search"] == "Sport"
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.wordpress_category_service.decrypt_api_key",
        lambda _key: "secret",
    )
    monkeypatch.setattr(
        "app.services.wordpress_category_service.httpx.AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    cat_id = await resolve_wp_category_id(site, "Sport")
    assert cat_id == 7

    # Cached on second call
    cat_id_2 = await resolve_wp_category_id(site, "Sport")
    assert cat_id_2 == 7


@pytest.mark.asyncio
async def test_resolve_wp_category_ids_for_app_category_sports(monkeypatch):
    _category_id_cache.clear()

    site = WordPressSite(
        id=2,
        user_id=1,
        name="Test",
        url="https://example.com",
        api_key="encrypted",
        username="admin",
    )

    async def fake_resolve(_site, name):
        assert name == "Sport"
        return 7

    monkeypatch.setattr(
        "app.services.wordpress_category_service.resolve_wp_category_id",
        fake_resolve,
    )

    ids = await resolve_wp_category_ids_for_app_category(site, "Sports")
    assert ids == [7]


@pytest.mark.asyncio
async def test_resolve_wp_category_ids_unmapped_returns_empty():
    site = WordPressSite(
        id=3,
        user_id=1,
        name="Test",
        url="https://example.com",
        api_key="encrypted",
        username="admin",
    )
    ids = await resolve_wp_category_ids_for_app_category(site, "Technology & AI")
    assert ids == []


@pytest.mark.asyncio
async def test_ensure_wp_category_creates_when_missing(monkeypatch):
    _category_id_cache.clear()
    site = WordPressSite(
        id=4,
        user_id=1,
        name="Test",
        url="https://example.com",
        api_key="encrypted",
        username="admin",
    )

    async def fake_resolve(_site, name):
        return None

    class FakeResponse:
        status_code = 201

        def json(self):
            return {"id": 42, "name": PILLAR_CONTENT_WP_CATEGORY}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, auth=None, json=None):
            assert "categories" in url
            assert json["name"] == PILLAR_CONTENT_WP_CATEGORY
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.wordpress_category_service.resolve_wp_category_id",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.wordpress_category_service.decrypt_api_key",
        lambda _key: "secret",
    )
    monkeypatch.setattr(
        "app.services.wordpress_category_service.httpx.AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    cat_id = await ensure_wp_category(site, PILLAR_CONTENT_WP_CATEGORY, "pillar-content")
    assert cat_id == 42


@pytest.mark.asyncio
async def test_resolve_cluster_main_uses_pillar(monkeypatch):
    _category_id_cache.clear()
    site = WordPressSite(
        id=5,
        user_id=1,
        name="Test",
        url="https://example.com",
        api_key="encrypted",
        username="admin",
    )
    main = TrendingKeywords(
        id=1,
        keyword="Tyla",
        data={},
        source="cluster",
        parent_id=None,
        category="Celebrities & Public Figures",
    )

    async def fake_ensure(_site, name, slug=None):
        assert name == PILLAR_CONTENT_WP_CATEGORY
        return 99

    monkeypatch.setattr(
        "app.services.wordpress_category_service.ensure_wp_category",
        fake_ensure,
    )

    ids = await resolve_wp_category_ids_for_cluster_keyword(site, main)
    assert ids == [99]


@pytest.mark.asyncio
async def test_resolve_cluster_sub_uses_niche(monkeypatch):
    site = WordPressSite(
        id=6,
        user_id=1,
        name="Test",
        url="https://example.com",
        api_key="encrypted",
        username="admin",
    )
    sub = TrendingKeywords(
        id=2,
        keyword="Tyla career",
        data={},
        source="cluster",
        parent_id=1,
        category="Celebrities & Public Figures",
    )

    async def fake_app(_site, app_category):
        assert app_category == "Celebrities & Public Figures"
        return [3]

    monkeypatch.setattr(
        "app.services.wordpress_category_service.resolve_wp_category_ids_for_app_category",
        fake_app,
    )

    ids = await resolve_wp_category_ids_for_cluster_keyword(site, sub)
    assert ids == [3]
