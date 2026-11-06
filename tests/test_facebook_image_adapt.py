"""Facebook feed adapt cover-crops tall 4:5 cards to full-bleed 1:1."""

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


def test_portrait_card_becomes_full_bleed_square_jpeg():
    raw = _png_bytes(1080, 1350)
    out = adapt_social_card_for_facebook_feed(raw)
    assert out.startswith(b"\xff\xd8\xff")
    with Image.open(BytesIO(out)) as img:
        assert img.size == (FACEBOOK_FEED_WIDTH, FACEBOOK_FEED_HEIGHT)


def test_bottom_weighted_crop_keeps_bottom_pixels():
    """Bottom band is red; after crop the square should still be mostly red."""
    img = Image.new("RGB", (1080, 1350), (0, 0, 255))
    # Bottom 1080px painted red — that is what a bottom-weighted crop should keep.
    for y in range(270, 1350):
        for x in range(1080):
            img.putpixel((x, y), (220, 30, 30))
    buf = BytesIO()
    img.save(buf, format="PNG")
    out = adapt_social_card_for_facebook_feed(buf.getvalue())
    with Image.open(BytesIO(out)) as result:
        # Center pixel of the square should come from the red bottom region.
        assert result.getpixel((540, 540))[0] > 180
