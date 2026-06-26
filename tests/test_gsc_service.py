"""Tests for GSC API client pagination."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.gsc_service import query_search_analytics


@pytest.mark.asyncio
async def test_query_search_analytics_pagination():
    batch1 = {
        "rows": [
            {
                "keys": ["2025-06-01", "https://example.com/a"],
                "clicks": 10,
                "impressions": 100,
                "ctr": 0.1,
                "position": 5.0,
            }
        ]
    }
    batch2 = {"rows": []}

    call_count = {"n": 0}

    def make_response():
        resp = MagicMock()
        resp.status_code = 200
        resp.json = lambda: batch1 if call_count["n"] == 1 else batch2
        resp.raise_for_status = lambda: None
        return resp

    async def mock_post(*args, **kwargs):
        call_count["n"] += 1
        return make_response()

    mock_client = AsyncMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.gsc_service.httpx.AsyncClient", return_value=mock_client):
        rows = await query_search_analytics(
            "token",
            "https://example.com/",
            date(2025, 6, 1),
            date(2025, 6, 28),
            ["date", "page"],
            row_limit=1,
        )

    assert len(rows) == 1
    assert rows[0]["page"] == "https://example.com/a"
    assert rows[0]["clicks"] == 10
    assert call_count["n"] == 2
