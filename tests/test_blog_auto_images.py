"""Tests for blog auto-image helpers."""

import pytest
from unittest.mock import MagicMock

from app.models.workflow_state import WorkflowStatus
from app.services.blog_image_service import (
    add_image_urls_to_used,
    apply_auto_images_batch,
    apply_auto_images_to_workflow,
    collect_used_urls_from_body,
    insert_image_after_section,
    pick_from_pool,
    POSITIONAL_PICK_INDICES,
    resolve_verified_url,
    SECTION_HEADINGS,
)

SAMPLE_POOL = [
    {"original": f"https://img/{i}.jpg", "link": f"https://img/{i}.jpg", "title": f"Img {i}"}
    for i in range(1, 12)
]

BIO_BODY = (
    "## Introduction\n\nIntro text.\n\n"
    "## Career Overview\n\nCareer text.\n\n"
    "## Personal Life\n\nPersonal text.\n\n"
    "## Public Image & Legacy\n\nLegacy text.\n\n"
    "## Conclusion\n\nEnd."
)

NW_BODY = (
    "## Introduction\n\nIntro.\n\n"
    "## Career & Achievements\n\nCareer.\n\n"
    "## Personal Life\n\nPersonal.\n\n"
    "## Conclusion\n\nEnd."
)


def _make_workflow(state_data: dict, workflow_id: int = 1) -> MagicMock:
    wf = MagicMock()
    wf.id = workflow_id
    wf.status = WorkflowStatus.COMPLETED
    wf.state_data = state_data
    return wf


def _mock_db():
    db = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    return db


def test_collect_used_urls_from_body_html_and_markdown():
    body = (
        'Intro\n\n<img src="https://example.com/a.jpg" alt="A" />\n\n'
        '![b](https://example.com/b.jpg)'
    )
    urls = collect_used_urls_from_body(body)
    assert "https://example.com/a.jpg" in urls
    assert "https://example.com/b.jpg" in urls


def test_add_image_urls_to_used():
    used: set = set()
    add_image_urls_to_used(used, {
        "url": "https://example.com/feat.jpg",
        "thumbnail": "https://example.com/thumb.jpg",
    })
    assert "https://example.com/feat.jpg" in used
    assert "https://example.com/thumb.jpg" in used


def test_insert_image_after_career_section():
    body = (
        "## Introduction\n\n"
        "Opening text.\n\n"
        "## Career Overview\n\n"
        "He started young.\n\n"
        "More career details.\n\n"
        "## Personal Life\n\n"
        "Family info."
    )
    result = insert_image_after_section(
        body,
        ["Career Overview"],
        "https://example.com/career.jpg",
        "Career photo",
    )
    assert "https://example.com/career.jpg" in result
    assert result.index("Career photo") < result.index("## Personal Life")
    assert result.count("https://example.com/career.jpg") == 1


def test_insert_image_skips_missing_section():
    body = "## Introduction\n\nHello."
    result = insert_image_after_section(
        body,
        ["Career Overview"],
        "https://example.com/x.jpg",
        "Alt",
    )
    assert result == body


def test_section_headings_biography_has_public_image():
    sections = [s[0] for s in SECTION_HEADINGS["biography"]]
    assert "career" in sections
    assert "personal_life" in sections
    assert "public_image" in sections


def test_section_headings_net_worth_no_public_image():
    sections = [s[0] for s in SECTION_HEADINGS["net_worth"]]
    assert "public_image" not in sections
    assert "career" in sections


@pytest.fixture(autouse=True)
def mock_image_verify(monkeypatch):
    """Default: all image URLs pass verification in tests."""

    async def always_valid(url: str) -> bool:
        return url.startswith("http")

    monkeypatch.setattr(
        "app.services.blog_image_service.verify_image_url",
        always_valid,
    )


@pytest.mark.asyncio
async def test_pick_from_pool_dedup():
    """Two picks from same pool return different URLs using positional slots."""
    used: set = set()
    first = await pick_from_pool(SAMPLE_POOL, used, "Title 1", pick_slot=0)
    second = await pick_from_pool(SAMPLE_POOL, used, "Title 2", pick_slot=1)
    assert first is not None
    assert second is not None
    assert first["url"] != second["url"]
    assert first["url"] == "https://img/1.jpg"
    assert second["url"] == "https://img/3.jpg"
    assert len(used) >= 2


@pytest.mark.asyncio
async def test_pick_from_pool_follows_positional_schedule():
    used: set = set()
    picks = []
    for slot in range(4):
        picked = await pick_from_pool(SAMPLE_POOL, used, f"Title {slot}", pick_slot=slot)
        assert picked is not None
        picks.append(picked["url"])
    assert picks[0] == "https://img/1.jpg"
    assert picks[1] == "https://img/3.jpg"
    assert picks[2] == "https://img/5.jpg"
    assert picks[3] == "https://img/7.jpg"
    assert POSITIONAL_PICK_INDICES[:4] == [0, 2, 4, 6]


@pytest.mark.asyncio
async def test_apply_auto_images_fetches_serpapi_once(monkeypatch):
    fetch_calls = 0

    async def counting_fetch(db, user_id, keyword):
        nonlocal fetch_calls
        fetch_calls += 1
        return SAMPLE_POOL

    monkeypatch.setattr(
        "app.services.blog_image_service._fetch_image_pool",
        counting_fetch,
    )

    wf = _make_workflow({
        "title": "Who Is Brock Purdy?",
        "focus_keyword": "Brock Purdy",
        "body_content": BIO_BODY,
        "meta_description": "Bio",
    })

    used: set = set()
    result = await apply_auto_images_to_workflow(
        _mock_db(), wf, user_id=1, content_type="biography", used_urls=used
    )

    assert fetch_calls == 1
    assert result["featured_image"] is not None
    assert result["featured_image"]["url"] == "https://img/1.jpg"
    assert len(result["section_images"]) == 3
    assert result["section_images"][0]["url"] == "https://img/3.jpg"
    assert result["section_images"][1]["url"] == "https://img/5.jpg"
    assert result["section_images"][2]["url"] == "https://img/7.jpg"


@pytest.mark.asyncio
async def test_apply_auto_images_with_prefetched_pool_skips_fetch(monkeypatch):
    fetch_calls = 0

    async def counting_fetch(db, user_id, keyword):
        nonlocal fetch_calls
        fetch_calls += 1
        return SAMPLE_POOL

    monkeypatch.setattr(
        "app.services.blog_image_service._fetch_image_pool",
        counting_fetch,
    )

    wf = _make_workflow({
        "title": "Who Is Brock Purdy?",
        "focus_keyword": "Brock Purdy",
        "body_content": BIO_BODY,
        "meta_description": "Bio",
    })

    used: set = set()
    await apply_auto_images_to_workflow(
        _mock_db(),
        wf,
        user_id=1,
        content_type="biography",
        used_urls=used,
        pool=list(SAMPLE_POOL),
    )
    assert fetch_calls == 0


@pytest.mark.asyncio
async def test_batch_fetches_serpapi_once(monkeypatch):
    fetch_calls = 0

    async def counting_fetch(db, user_id, keyword):
        nonlocal fetch_calls
        fetch_calls += 1
        return SAMPLE_POOL

    monkeypatch.setattr(
        "app.services.blog_image_service._fetch_image_pool",
        counting_fetch,
    )

    bio_wf = _make_workflow({
        "title": "Who Is Brock Purdy?",
        "focus_keyword": "Brock Purdy",
        "body_content": BIO_BODY,
        "meta_description": "Bio",
    }, workflow_id=10)
    nw_wf = _make_workflow({
        "title": "Brock Purdy Net Worth",
        "focus_keyword": "Brock Purdy",
        "body_content": NW_BODY,
        "meta_description": "NW",
    }, workflow_id=11)

    workflows = {10: bio_wf, 11: nw_wf}
    db = _mock_db()

    def query_side_effect(model):
        q = MagicMock()
        q.filter.return_value.first.side_effect = lambda: workflows.get(
            query_side_effect.last_id, bio_wf
        )
        return q

    # Return correct workflow based on filter chain
    call_order = [bio_wf, bio_wf, nw_wf]

    def filter_side_effect(*args, **kwargs):
        f = MagicMock()
        f.first.side_effect = lambda: call_order.pop(0) if call_order else nw_wf
        return f

    db.query.return_value.filter.side_effect = filter_side_effect

    batch = await apply_auto_images_batch(
        db,
        user_id=1,
        items=[
            {"workflow_id": 10, "content_type": "biography"},
            {"workflow_id": 11, "content_type": "net_worth"},
        ],
    )

    assert fetch_calls == 1
    assert len(batch["results"]) == 2
    bio_result = batch["results"][0]
    nw_result = batch["results"][1]
    assert bio_result["featured_image"]["url"] == "https://img/1.jpg"
    assert nw_result["featured_image"]["url"] == "https://img/2.jpg"
    assert len(set(batch["used_urls"])) >= 4


@pytest.mark.asyncio
async def test_resolve_verified_url_prefers_thumbnail(monkeypatch):
    calls: list[str] = []

    async def fake_verify(url: str) -> bool:
        calls.append(url)
        return url == "https://thumb/ok.jpg"

    monkeypatch.setattr(
        "app.services.blog_image_service.verify_image_url",
        fake_verify,
    )

    url = await resolve_verified_url({
        "thumbnail": "https://thumb/ok.jpg",
        "original": "https://original/bad.jpg",
        "link": "https://page/bad",
    })
    assert url == "https://thumb/ok.jpg"
    assert calls == ["https://thumb/ok.jpg"]


@pytest.mark.asyncio
async def test_resolve_verified_url_falls_back_to_original(monkeypatch):
    async def fake_verify(url: str) -> bool:
        return url == "https://original/good.jpg"

    monkeypatch.setattr(
        "app.services.blog_image_service.verify_image_url",
        fake_verify,
    )

    url = await resolve_verified_url({
        "thumbnail": "https://thumb/bad.jpg",
        "original": "https://original/good.jpg",
    })
    assert url == "https://original/good.jpg"


@pytest.mark.asyncio
async def test_pick_from_pool_skips_empty_candidate():
    pool = [
        {"original": "", "thumbnail": "", "link": "", "title": "Empty"},
        {"original": "https://good/2.jpg", "thumbnail": "https://good/2t.jpg", "title": "Good"},
    ]

    used: set = set()
    picked = await pick_from_pool(pool, used, "Test", pick_slot=0)
    assert picked is not None
    assert "good" in picked["url"]
