"""Tests for social headline generation (including Pro 1 highlights)."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.services.openrouter_service import generate_news_headline

def test_ensure_pro1_highlight_markup_classic_headline():
    from app.services.openrouter_service import _ensure_pro1_highlight_markup

    raw = "SpaceX Starship megarocket— Finds mixed success in debut test flight"
    out = _ensure_pro1_highlight_markup(raw)
    assert "**" in out
    assert "SpaceX" in out
    assert "mixed success" in out or "mixed" in out


def test_ensure_pro1_highlight_markup_preserves_existing():
    from app.services.openrouter_service import _ensure_pro1_highlight_markup

    raw = "**Tools that** reveal your data"
    assert _ensure_pro1_highlight_markup(raw) == raw


def test_generate_news_headline_pro1_preserves_highlights_and_lines():
    raw = "**SpaceX Starship** megarocket\nFinds **mixed success** in debut test flight"

    async def _run():
        with patch(
            "app.services.openrouter_service.generate_content",
            new_callable=AsyncMock,
            return_value=raw,
        ), patch(
            "app.services.openrouter_service.has_usable_llm_key",
            return_value=True,
        ):
            return await generate_news_headline(
                [{"title": "T", "source": "S", "snippet": "Sn", "date": "d"}],
                "spacex",
                template_variant="pro-1",
            )

    headline = asyncio.run(_run())
    assert "**SpaceX Starship**" in headline
    assert "**mixed success**" in headline
    assert "\n" in headline


def test_generate_news_headline_pro2_uses_pro_highlight_prompt():
    captured: list[str] = []

    async def _capture(prompt: str, **kwargs):
        captured.append(prompt)
        return "**Tools that** reveal\nyour hidden data"

    async def _run():
        with patch(
            "app.services.openrouter_service.generate_content",
            side_effect=_capture,
        ), patch(
            "app.services.openrouter_service.has_usable_llm_key",
            return_value=True,
        ):
            await generate_news_headline(
                [{"title": "T", "source": "S", "snippet": "Sn", "date": "d"}],
                template_variant="pro-2",
            )

    asyncio.run(_run())
    assert captured
    assert "double asterisks" in captured[0].lower() or "**" in captured[0]


def test_generate_news_headline_pro1_prompt_mentions_highlights():
    captured: list[str] = []

    async def _capture(prompt: str, **kwargs):
        captured.append(prompt)
        return "**Tools that** reveal\nyour hidden data"

    async def _run():
        with patch(
            "app.services.openrouter_service.generate_content",
            side_effect=_capture,
        ), patch(
            "app.services.openrouter_service.has_usable_llm_key",
            return_value=True,
        ):
            await generate_news_headline(
                [{"title": "T", "source": "S", "snippet": "Sn", "date": "d"}],
                template_variant="pro-1",
            )

    asyncio.run(_run())
    assert captured
    assert "double asterisks" in captured[0].lower() or "**" in captured[0]


def test_generate_news_headline_classic_two_lines_max():
    raw = "Line one here\nLine two here\nLine three extra"

    async def _run():
        with patch(
            "app.services.openrouter_service.generate_content",
            new_callable=AsyncMock,
            return_value=raw,
        ), patch(
            "app.services.openrouter_service.has_usable_llm_key",
            return_value=True,
        ):
            return await generate_news_headline(
                [{"title": "T", "source": "S", "snippet": "Sn", "date": "d"}],
            )

    headline = asyncio.run(_run())
    assert headline.count("\n") == 1
    assert "Line three" not in headline
