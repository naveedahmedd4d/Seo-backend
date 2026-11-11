"""Ranking and LLM-pick parsing for the daily news topic list."""

import json

import pytest

from app.services.news_topics_service import (
    choose_social_publish_id,
    normalize_source_ids,
    parse_feed_xml,
    parse_llm_pick,
    rank_stories,
    topic_entry_rows,
)


SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>Anthropic sets a $1.4 trillion IPO - TechCrunch</title><link>https://example.com/a</link></item>
  <item><title>Local power outage hits one county</title><link>https://example.com/b</link></item>
</channel></rss>
"""


def test_parse_feed_strips_publisher_suffix():
    items = parse_feed_xml(SAMPLE_RSS, "techcrunch")
    assert items[0]["title"] == "Anthropic sets a $1.4 trillion IPO"
    assert items[0]["source"] == "techcrunch"
    assert items[0]["link"] == "https://example.com/a"


def test_rank_merges_the_same_story_across_feeds():
    items = [
        {"title": "Anthropic IPO valuation climbs again", "link": "https://a", "source": "google_ai"},
        {"title": "Anthropic IPO valuation climbs", "link": "https://b", "source": "techcrunch"},
        {"title": "ChatGPT business agents roll out", "link": "https://c", "source": "verge"},
        {"title": "Linux servers receive a security patch", "link": "https://d", "source": "ars"},
    ]
    ranked = rank_stories(items)
    assert ranked[0]["title"].startswith("Anthropic")
    assert ranked[0]["sources"] == ["google_ai", "techcrunch"]
    assert ranked[0]["headline_count"] == 2
    assert [story["id"] for story in ranked] == [1, 2, 3]


def test_parse_llm_pick_rejects_ids_outside_the_list():
    raw = json.dumps(
        {
            "picks": [
                {"id": 2, "angle": "Explain the product change"},
                {"id": 9, "angle": "Invented story"},
                {"id": 1, "angle": "Cover the IPO"},
                {"id": 3, "angle": "Security checklist"},
            ],
            "publish_id": 9,
            "why": "Not in the list",
        }
    )
    choice = parse_llm_pick(raw, {1, 2, 3})
    assert [pick["id"] for pick in choice["picks"]] == [2, 1, 3]
    assert choice["publish_id"] == 2


def test_parse_llm_pick_requires_a_listed_story():
    with pytest.raises(ValueError):
        parse_llm_pick('{"picks":[{"id": 8, "angle": "nope"}], "publish_id": 8}', {1, 2, 3})


def test_empty_source_selection_means_all_feeds():
    assert "google_ai" in normalize_source_ids(None)
    assert normalize_source_ids(["verge", "verge", "nope"]) == ["verge"]


def test_source_selection_requires_one_real_feed():
    with pytest.raises(ValueError):
        normalize_source_ids(["not-a-feed"])


def test_topic_rows_use_feed_name_and_source():
    rows = topic_entry_rows(
        "2026-11-11",
        [{"id": 1, "title": "Claude joins meetings", "sources": ["techcrunch", "verge"], "link": "https://example.com"}],
    )
    assert [(row["name"], row["source"]) for row in rows] == [
        ("TechCrunch AI", "techcrunch"),
        ("The Verge AI", "verge"),
    ]
    assert rows[0]["topic_date"] == "2026-11-11"
    assert rows[0]["title"] == "Claude joins meetings"


def test_social_publish_id_excludes_blog_publish_id():
    stories = [{"id": i, "title": f"Story {i}"} for i in range(1, 11)]
    picks = [{"id": 1, "angle": "a"}, {"id": 2, "angle": "b"}, {"id": 3, "angle": "c"}]
    social_id = choose_social_publish_id(stories, blog_publish_id=1, picks=picks)
    assert social_id == 2
    assert social_id != 1


def test_social_publish_id_falls_back_to_next_ranked_story():
    stories = [{"id": i, "title": f"Story {i}"} for i in range(1, 5)]
    picks = [{"id": 1, "angle": "only"}]
    social_id = choose_social_publish_id(stories, blog_publish_id=1, picks=picks)
    assert social_id == 2
