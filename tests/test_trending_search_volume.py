"""Trending search-volume parsing from SerpAPI google_trends_trending_now."""

from app.api.trending import _build_keyword_dict, _coerce_int, _pick_search_volume
from app.services.serpapi_service import _coerce_search_volume, _parse_trending_item


def test_parse_trending_item_reads_search_volume():
    item = _parse_trending_item(
        {
            "query": "venezuela",
            "start_timestamp": 1767421200,
            "active": True,
            "search_volume": 2_000_000,
            "increase_percentage": 1000,
            "categories": [{"id": 11, "name": "Other"}],
            "trend_breakdown": ["maduro"],
        }
    )
    assert item["keyword"] == "venezuela"
    assert item["value"] == 2_000_000
    assert item["search_volume"] == 2_000_000
    assert item["increase_percentage"] == 1000
    assert item["categories"] == ["Other"]
    assert item["timestamp"].endswith("Z")


def test_parse_trending_item_missing_volume_is_none():
    item = _parse_trending_item({"query": "no volume here"})
    assert item["value"] is None
    assert item["increase_percentage"] is None


def test_parse_trending_item_skips_blank_query():
    assert _parse_trending_item({"query": "  "}) is None
    assert _parse_trending_item({}) is None


def test_coerce_search_volume_formatted_traffic_fallback():
    assert _coerce_search_volume({"formatted_traffic": "2K+"}) == 2000
    assert _coerce_search_volume({"formatted_traffic": "1,500,000"}) == 1_500_000
    assert _coerce_search_volume({"value": 500}) == 500
    assert _coerce_search_volume({"search_volume": 0, "value": None}) is None


def test_coerce_int_treats_zero_as_missing():
    assert _coerce_int(0) is None
    assert _coerce_int("0") is None
    assert _coerce_int(250) == 250
    assert _coerce_int("1,200") == 1200
    assert _coerce_int(True) is None


def test_pick_search_volume_prefers_mangools_sv():
    assert _pick_search_volume({"mangools_sv": 900, "value": 0}) == 900
    assert _pick_search_volume({"value": 0, "search_volume": 40_000}) == 40_000
    assert _pick_search_volume({}, 12_000) == 12_000
    assert _pick_search_volume({"value": 0}) is None


def test_build_keyword_dict_surfaces_volume_and_increase():
    row = {
        "id": 7,
        "keyword": "caitlin clark contract",
        "data": {
            "value": 40_000,
            "search_volume": 40_000,
            "increase_percentage": 250,
            "timestamp": "2026-09-05T07:00:00Z",
        },
        "source": "trending",
    }
    out = _build_keyword_dict(row)
    assert out["value"] == 40_000
    assert out["increase_percentage"] == 250
