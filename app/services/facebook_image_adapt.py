"""Adapt 4:5 social cards for Facebook feed so desktop stops showing empty side bars."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter

# Meta's recommended feed photo size. 4:5 portraits (1080×1350) are pillarboxed
# on desktop Facebook with empty side chrome; a square upload fills that slot.
FACEBOOK_FEED_WIDTH = 1080
FACEBOOK_FEED_HEIGHT = 1080


def adapt_social_card_for_facebook_feed(image_bytes: bytes) -> bytes:
    """
    Place the exported social card on a 1:1 canvas.

    Sides are filled with a blurred, darkened cover of the same artwork so
    Facebook does not show empty blue/navy bars around a tall 4:5 PNG.
    """
    if not image_bytes:
        return image_bytes

    with Image.open(BytesIO(image_bytes)) as opened:
        src = opened.convert("RGB")

    # Already square (or wider): leave alone.
    if src.width >= src.height:
        return image_bytes

    out_w, out_h = FACEBOOK_FEED_WIDTH, FACEBOOK_FEED_HEIGHT
    out = Image.new("RGB", (out_w, out_h))

    # Blurred cover background across the full square.
    cover_scale = max(out_w / src.width, out_h / src.height)
    bg_w = max(1, int(round(src.width * cover_scale)))
    bg_h = max(1, int(round(src.height * cover_scale)))
    bg = src.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
    left = max(0, (bg_w - out_w) // 2)
    top = max(0, (bg_h - out_h) // 2)
    bg = bg.crop((left, top, left + out_w, top + out_h))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=28))
    bg = ImageEnhance.Brightness(bg).enhance(0.72)
    out.paste(bg, (0, 0))

    # Sharp card fitted to the square height and centered horizontally.
    card_scale = out_h / src.height
    card_w = max(1, int(round(src.width * card_scale)))
    card_h = out_h
    card = src.resize((card_w, card_h), Image.Resampling.LANCZOS)
    x = max(0, (out_w - card_w) // 2)
    out.paste(card, (x, 0))

    buf = BytesIO()
    out.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()
