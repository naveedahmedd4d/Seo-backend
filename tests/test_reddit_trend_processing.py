from datetime import datetime, timedelta, timezone

from app.services.reddit_trend_processing import (
    aggregate_clusters,
    cluster_posts,
    deduplicate_posts,
    validate_post_item,
)


NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)


def actor_item(post_id: str, title: str, *, subreddit: str = "technology", **changes):
    item = {
        "id": post_id,
        "kind": "post",
        "title": title,
        "selftext": "A useful discussion",
        "author": "example-user",
        "subreddit": subreddit,
        "permalink": f"/r/{subreddit}/comments/{post_id}/topic/",
        "created_utc": (NOW - timedelta(hours=2)).timestamp(),
        "score": 80,
        "num_comments": 20,
        "score_per_hour": 40,
        "comments_per_hour": 10,
        "upvote_ratio": 0.8,
    }
    item.update(changes)
    return item


def test_validation_rejects_nsfw_removed_and_non_reddit_urls():
    assert validate_post_item(actor_item("a", "NSFW", over_18=True), now=NOW) is None
    assert validate_post_item(actor_item("b", "Removed", is_removed=True), now=NOW) is None
    assert validate_post_item(actor_item("c", "Invalid", permalink="https://example.com/x"), now=NOW) is None


def test_validation_accepts_aliases_and_derives_velocity():
    item = actor_item("alias", "OpenAI ships a new model")
    item.pop("id")
    item.pop("created_utc")
    item.pop("score_per_hour")
    item.pop("comments_per_hour")
    item["postId"] = "alias"
    item["createdAt"] = int((NOW - timedelta(hours=2)).timestamp() * 1000)
    post = validate_post_item(item, now=NOW)
    assert post is not None
    assert post["canonical_url"].startswith("https://www.reddit.com/r/")
    assert post["score_per_hour"] == 40
    assert post["comments_per_hour"] == 10


def test_deduplication_uses_post_id_and_canonical_url():
    first = validate_post_item(actor_item("same", "First copy"), now=NOW)
    duplicate_id = validate_post_item(
        actor_item("same", "Second copy", permalink="/r/technology/comments/same/other/"),
        now=NOW + timedelta(minutes=1),
    )
    duplicate_url = validate_post_item(
        actor_item(
            "different",
            "Third copy",
            permalink="/r/technology/comments/same/topic/",
        ),
        now=NOW,
    )
    assert first and duplicate_id and duplicate_url
    assert len(deduplicate_posts([first, duplicate_id, duplicate_url])) == 1


def test_clustering_groups_same_topic_across_subreddits():
    posts = [
        validate_post_item(actor_item("one", "OpenAI launches new reasoning model"), now=NOW),
        validate_post_item(
            actor_item(
                "two",
                "OpenAI launches a new reasoning model",
                subreddit="MachineLearning",
            ),
            now=NOW,
        ),
        validate_post_item(actor_item("three", "Python packaging tutorial"), now=NOW),
    ]
    clusters = cluster_posts([post for post in posts if post])
    assert sorted(len(cluster) for cluster in clusters) == [1, 2]


def test_trend_score_normalizes_then_applies_documented_weights():
    posts = [
        validate_post_item(actor_item("one", "OpenAI launches new reasoning model"), now=NOW),
        validate_post_item(
            actor_item(
                "two",
                "OpenAI launches a new reasoning model",
                subreddit="MachineLearning",
            ),
            now=NOW,
        ),
    ]
    aggregates, stats = aggregate_clusters(
        [{"members": [post for post in posts if post], "topic": "OpenAI reasoning model"}],
        enabled_subreddits=3,
    )
    trend = aggregates[0]
    assert trend["normalized_score_per_hour"] == 100
    assert trend["normalized_comments_per_hour"] == 100
    assert trend["normalized_upvote_ratio"] == 80
    assert trend["normalized_cross_subreddit"] == 50
    assert trend["trend_score"] == 92
    assert "score_velocity*.40" in stats["formula"]
