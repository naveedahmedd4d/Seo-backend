"""Upload-Post form payload must be a shape httpx sends asynchronously.

Publishing failed with "Attempted to send an sync request with an AsyncClient
instance." httpx 0.27 routes a list-of-tuples `data=` through the sync
transport, which an AsyncClient refuses. A dict (lists for repeated keys)
sends correctly.
"""

import pytest

from app.services import upload_post_service as svc


def test_payload_is_a_dict_not_a_list_of_tuples():
    out = svc.build_form_payload(
        user="u", platform="facebook", title="T", description="D",
        image_url="https://ex.com/a.jpg", extra={},
    )
    assert isinstance(out, dict), "list-of-tuples triggers httpx sync-transport routing"


def test_repeated_keys_become_lists():
    out = svc.build_form_payload(
        user="u", platform="facebook", title=None, description=None,
        image_url="https://ex.com/a.jpg", extra={},
    )
    assert out["platform[]"] == ["facebook"]
    assert out["photos[]"] == ["https://ex.com/a.jpg"]


def test_omits_empty_optional_fields():
    out = svc.build_form_payload(
        user="u", platform="tiktok", title=None, description=None,
        image_url=None, extra={},
    )
    assert "title" not in out
    assert "description" not in out
    assert "photos[]" not in out


def test_extra_platform_fields_included():
    out = svc.build_form_payload(
        user="u", platform="facebook", title="T", description=None,
        image_url=None, extra={"facebook_page_id": "123"},
    )
    assert out["facebook_page_id"] == "123"


@pytest.mark.asyncio
async def test_payload_is_accepted_by_an_async_client():
    """The real regression: httpx must not reject the shape we send."""
    import httpx

    payload = svc.build_form_payload(
        user="u", platform="facebook", title="T", description="D",
        image_url="https://ex.com/a.jpg", extra={"facebook_page_id": "1"},
    )

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.post("https://x.test/upload_photos", data=payload)
    assert res.status_code == 200
    assert b"photos" in captured["body"]
