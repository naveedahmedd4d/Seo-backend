"""Facebook feed adapt expands tall 4:5 cards to square so desktop has no empty side bars."""

from io import BytesIO

from PIL import Image

from app.services.facebook_image_adapt import (
    FACEBOOK_FEED_HEIGHT,
    FACEBOOK_FEED_WIDTH,
    adapt_social_card_for_facebook_feed,
)


def _png_bytes(width: int, height: int, color=(20, 40, 80)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_portrait_card_becomes_square_jpeg():
    raw = _png_bytes(1080, 1350)
    out = adapt_social_card_for_facebook_feed(raw)
    assert out.startswith(b"\xff\xd8\xff")
    with Image.open(BytesIO(out)) as img:
        assert img.size == (FACEBOOK_FEED_WIDTH, FACEBOOK_FEED_HEIGHT)


def test_already_wide_image_unchanged():
    raw = _png_bytes(1200, 630, color=(10, 10, 10))
    out = adapt_social_card_for_facebook_feed(raw)
    assert out == raw
