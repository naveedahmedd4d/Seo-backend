"""Tests for related search queries with answers."""

import json

import pytest

from app.services.openrouter_service import (
    _default_related_queries,
    _parse_related_queries_response,
    generate_related_queries,
)
from unittest.mock import AsyncMock, patch


def test_parse_related_queries_with_answers():
    raw = json.dumps([
        {"query": "Who is Jorge Mateo", "answer": "Jorge Mateo is a professional baseball player."},
        {"query": "Jorge Mateo Braves", "answer": "He plays for the Atlanta Braves organization."},
    ])
    out = _parse_related_queries_response(raw, "Jorge Mateo")
    assert len(out) == 2
    assert out[0]["answer"]
    assert "Jorge Mateo" in out[0]["answer"]


def test_parse_related_queries_legacy_strings():
    raw = json.dumps(["Who is Jorge Mateo", "Jorge Mateo career"])
    out = _parse_related_queries_response(raw, "Jorge Mateo")
    assert len(out) == 2
    assert out[0]["query"] == "Who is Jorge Mateo"
    assert out[0]["answer"] == ""


def test_default_related_queries_have_answers():
    out = _default_related_queries("Jorge Mateo")
    assert len(out) == 8
    assert all(item.get("answer", "").strip() for item in out)


@pytest.mark.asyncio
async def test_generate_related_queries_returns_answers():
    llm_json = json.dumps([
        {
            "query": "Who is Jorge Mateo",
            "answer": "Jorge Mateo is a Dominican professional baseball infielder known for his speed and power.",
        },
        {
            "query": "Jorge Mateo Atlanta Braves",
            "answer": "He has been part of the Braves system and featured in recent MLB action.",
        },
    ] * 4)
    with patch(
        "app.services.openrouter_service.generate_blog_seo_content",
        new_callable=AsyncMock,
        return_value=llm_json,
    ):
        result = await generate_related_queries(
            "Jorge Mateo",
            category="Celebrities & Public Figures",
            content_intent="parent_overview",
        )
    assert len(result) == 8
    assert all(r.get("answer", "").strip() for r in result)
    assert result[0]["query"]
