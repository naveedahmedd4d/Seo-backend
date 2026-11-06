"""Article picker options: tracked rows plus whatever is live on the site."""

from app.services import published_options_service as svc


def test_merges_tracked_and_live_without_duplicates():
    """A post this app published is also live on the site; show it once."""
    tracked = [
        {"id": 1, "wp_post_id": 10, "site_id": 3, "title": "Tracked", "website": "S",
         "url": "https://s.com/a", "published_at": "2026-01-01T00:00:00+00:00"},
    ]
    live = [
        {"wp_post_id": 10, "title": "Tracked (live copy)", "url": "https://s.com/a"},
        {"wp_post_id": 11, "title": "Only on site", "url": "https://s.com/b"},
    ]
    out = svc.merge_options(tracked, live, site_id=3, website="S")
    ids = [o["wp_post_id"] for o in out]
    assert ids.count(10) == 1, "the tracked row wins; no duplicate"
    assert 11 in ids, "a post published outside this app is still offered"


def test_live_only_entries_are_selectable():
    """Entries with no tracked row must still carry what generation needs."""
    out = svc.merge_options([], [{"wp_post_id": 11, "title": "T", "url": "u"}],
                            site_id=3, website="S")
    assert out[0]["wp_post_id"] == 11
    assert out[0]["site_id"] == 3
    assert out[0]["website"] == "S"
    assert out[0]["id"] is None, "no published_posts row exists for it"


def test_untitled_live_post_still_labelled():
    out = svc.merge_options([], [{"wp_post_id": 5, "title": "", "url": "u"}],
                            site_id=1, website="S")
    assert out[0]["title"] == "Untitled article"


def test_empty_inputs_yield_empty_list():
    assert svc.merge_options([], [], site_id=1, website="S") == []
