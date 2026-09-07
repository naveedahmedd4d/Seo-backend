"""Tests for Groq blog-generation API key fallback on 429."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.openrouter_service import (
    BLOG_GENERATION_MODEL_GROQ,
    _get_blog_groq_api_keys,
    generate_blog_content_with_groq,
)


def test_get_blog_groq_api_keys_deduplicates_and_orders_primary_first():
    with patch("app.services.openrouter_service.settings") as mock_settings:
        mock_settings.groq_api_key = "primary"
        mock_settings.blog_fallback_groq_api_key = None
        mock_settings.trending_fallback_groq_api_key = "fallback"
        assert _get_blog_groq_api_keys() == ["primary", "fallback"]


def test_get_blog_groq_api_keys_prefers_blog_fallback_over_trending():
    with patch("app.services.openrouter_service.settings") as mock_settings:
        mock_settings.groq_api_key = "primary"
        mock_settings.blog_fallback_groq_api_key = "blog-fallback"
        mock_settings.trending_fallback_groq_api_key = "trending-fallback"
        assert _get_blog_groq_api_keys() == ["primary", "blog-fallback"]


def test_generate_blog_content_uses_fallback_key_after_primary_429():
    calls: list[str] = []

    def _make_response(status_code: int, body: dict | str):
        response = MagicMock()
        response.status_code = status_code
        if isinstance(body, dict):
            response.text = ""
            response.json.return_value = body
        else:
            response.text = body
            response.json.side_effect = ValueError("not json")
        return response

    async def _fake_post(url, headers=None, json=None):
        token = (headers or {}).get("Authorization", "")
        calls.append(token)
        if token == "Bearer primary":
            return _make_response(
                429,
                {"error": {"message": "Rate limit reached for model"}},
            )
        return _make_response(
            200,
            {"choices": [{"message": {"content": "Generated blog body"}}]},
        )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=_fake_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    async def _run():
        with patch("app.services.openrouter_service.settings") as mock_settings:
            mock_settings.groq_api_key = "primary"
            mock_settings.blog_fallback_groq_api_key = "fallback"
            mock_settings.trending_fallback_groq_api_key = None
            mock_settings.openrouter_api_key = None
            mock_settings.groq_blog_delay_seconds = 0
            with patch(
                "app.services.openrouter_service.httpx.AsyncClient",
                return_value=mock_client,
            ), patch(
                "app.services.openrouter_service._rate_limit",
                new_callable=AsyncMock,
            ), patch(
                "app.services.openrouter_service.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                return await generate_blog_content_with_groq("Write a post")

    result = asyncio.run(_run())
    assert result == "Generated blog body"
    assert calls == ["Bearer primary", "Bearer fallback"]


def test_generate_blog_content_same_model_on_fallback():
    captured_models: list[str] = []

    def _make_response(status_code: int, body: dict):
        response = MagicMock()
        response.status_code = status_code
        response.text = ""
        response.json.return_value = body
        return response

    async def _fake_post(url, headers=None, json=None):
        captured_models.append(json["model"])
        if (headers or {}).get("Authorization") == "Bearer primary":
            return _make_response(429, {"error": {"message": "429"}})
        return _make_response(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
        )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=_fake_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    async def _run():
        with patch("app.services.openrouter_service.settings") as mock_settings:
            mock_settings.groq_api_key = "primary"
            mock_settings.blog_fallback_groq_api_key = "fallback"
            mock_settings.trending_fallback_groq_api_key = None
            mock_settings.openrouter_api_key = None
            mock_settings.groq_blog_delay_seconds = 0
            with patch(
                "app.services.openrouter_service.httpx.AsyncClient",
                return_value=mock_client,
            ), patch(
                "app.services.openrouter_service._rate_limit",
                new_callable=AsyncMock,
            ), patch(
                "app.services.openrouter_service.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                await generate_blog_content_with_groq("prompt")

    asyncio.run(_run())
    assert captured_models == [BLOG_GENERATION_MODEL_GROQ, BLOG_GENERATION_MODEL_GROQ]
