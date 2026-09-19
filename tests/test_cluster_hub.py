"""Cluster hub outline, linking, and generate-request validation."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.trending_keywords import TrendingKeywords
from app.schemas.blog import BlogGenerateRequest
from app.services.auth_service import create_access_token, get_password_hash
from app.services.cluster_hub_templates import (
    CLUSTER_HUB_TEMPLATE_ID,
    build_cluster_hub_outline,
    get_cluster_hub_section_slugs,
    normalize_cluster_members,
    outline_to_cluster_hub_prompt_block,
)
from app.services.cluster_keyword_service import _keyword_to_dict
from app.services.openrouter_service import (
    _build_cluster_hub_article_prompt,
    _fallback_cluster_hub_summary,
)

SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_headers(db_session):
    from app.models.user import User

    user = User(
        email="hub@test.com",
        password_hash=get_password_hash("password123"),
        full_name="Hub Tester",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": user.email})
    return {"Authorization": f"Bearer {token}"}, user


def test_normalize_cluster_members_dedupes():
    assert normalize_cluster_members(["  Kemi Badenoch ", "kemi badenoch", "Rishi Sunak", ""]) == [
        "Kemi Badenoch",
        "Rishi Sunak",
    ]


def test_build_cluster_hub_outline_has_members_and_no_faq():
    members = ["Kemi Badenoch", "Rishi Sunak"]
    outline = build_cluster_hub_outline("UK Conservative Politicians Net Worth 2026", members)
    assert len(outline) == 6
    h2_texts = [n["text"] for n in outline]
    assert h2_texts[0] == "Introduction"
    assert h2_texts[1] == "At a Glance"
    assert "How We Ranked" in h2_texts[2]
    assert h2_texts[3] == "The List"
    assert not any("FAQ" in t for t in h2_texts)

    the_list = next(n for n in outline if n["text"] == "The List")
    child_texts = [c["text"] for c in the_list.get("children") or []]
    assert child_texts == members


def test_outline_to_cluster_hub_prompt_block_includes_members():
    block = outline_to_cluster_hub_prompt_block(
        "WNBA Net Worth 2026",
        ["Caitlin Clark", "A'ja Wilson"],
    )
    assert "Caitlin Clark" in block
    assert "A'ja Wilson" in block
    assert "Cluster members:" in block
    assert "do not" in block.lower() and "faq" in block.lower()
    assert "not publicly disclosed" in block.lower()
    assert "## FAQ" not in block
    slugs = get_cluster_hub_section_slugs(["Caitlin Clark"])
    assert "intro" in slugs
    assert any(s.startswith("member_") for s in slugs)


def test_cluster_hub_article_prompt_includes_members_and_bans_placeholders():
    prompt = _build_cluster_hub_article_prompt(
        "UK Conservative Politicians Net Worth 2026",
        "",
        "UK Conservative Politicians Net Worth 2026",
        "News & Politics",
        "",
        "> **Last Updated:** August 12, 2026",
        cluster_subs=["Kemi Badenoch", "Rishi Sunak"],
    )
    assert "Kemi Badenoch" in prompt
    assert "Rishi Sunak" in prompt
    assert "Cluster members:" in prompt
    assert "Not publicly disclosed" in prompt
    assert "do not write" in prompt.lower() or "never write" in prompt.lower()
    assert "## FAQ" not in prompt


def test_fallback_cluster_hub_summary_uses_template():
    result = _fallback_cluster_hub_summary(
        "UK Conservative Politicians Net Worth 2026",
        {"answer": "A set of UK politicians."},
        ["Kemi Badenoch"],
        category="News & Politics",
    )
    assert result["outline_template"] == CLUSTER_HUB_TEMPLATE_ID
    assert result["content_intent"] == "cluster_hub"
    assert "Kemi Badenoch" in result["h1"] or "UK Conservative" in result["h1"]
    assert len(result["outline"]) == 6


def test_blog_generate_request_allows_cluster_hub_with_news_category():
    req = BlogGenerateRequest(
        topic="UK Conservative Politicians Net Worth 2026",
        category="News & Politics",
        content_intent="cluster_hub",
        cluster_subs=["Kemi Badenoch", "Rishi Sunak"],
    )
    assert req.content_intent == "cluster_hub"
    assert req.cluster_subs == ["Kemi Badenoch", "Rishi Sunak"]


def test_cluster_main_dict_does_not_alias_hub_as_bio(db_session):
    main = TrendingKeywords(
        keyword="UK Conservative Politicians Net Worth 2026",
        data={},
        source="cluster",
        blog_workflow_id=55,
    )
    db_session.add(main)
    db_session.commit()
    db_session.refresh(main)
    data = _keyword_to_dict(main, include_subs=True)
    assert data["blog_workflow_id"] == 55
    assert data["bio_blog_workflow_id"] is None


def test_cluster_sub_still_falls_back_legacy_blog_id(db_session):
    main = TrendingKeywords(keyword="Main", data={}, source="cluster")
    db_session.add(main)
    db_session.commit()
    db_session.refresh(main)
    sub = TrendingKeywords(
        keyword="Sub",
        data={},
        source="cluster",
        parent_id=main.id,
        blog_workflow_id=77,
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    data = _keyword_to_dict(sub)
    assert data["bio_blog_workflow_id"] == 77
    assert data["blog_workflow_id"] == 77


def test_patch_blog_workflow_cluster_hub(client, auth_headers, db_session):
    headers, user = auth_headers
    row = TrendingKeywords(
        keyword="WNBA Net Worth 2026",
        data={},
        source="cluster",
        user_id=user.id,
    )
    db_session.add(row)
    db_session.commit()

    response = client.patch(
        f"/api/trending/keywords/{row.id}/blog-workflow",
        headers=headers,
        json={"workflow_id": 303, "content_type": "cluster_hub"},
    )
    assert response.status_code == 200
    db_session.refresh(row)
    assert row.blog_workflow_id == 303
    assert row.bio_blog_workflow_id is None
    assert row.net_worth_blog_workflow_id is None
