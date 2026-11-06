"""Adapt 4:5 social cards for Facebook feed so the artwork fills the frame."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

# Desktop Facebook feed media is wider than 4:5. Uploading a full-bleed 1:1
# (Meta's recommended feed size) fills the slot; letterboxing/blur padding does not.
FACEBOOK_FEED_WIDTH = 1080
FACEBOOK_FEED_HEIGHT = 1080


def adapt_social_card_for_facebook_feed(image_bytes: bytes) -> bytes:
    """
    Cover-crop the exported social card to 1:1.

    The sharp artwork fills the entire canvas (no side blur / empty bars).
    Crop is bottom-weighted so the headline at the bottom of 4:5 cards is kept.
    """
    if not image_bytes:
        return image_bytes

    with Image.open(BytesIO(image_bytes)) as opened:
        src = opened.convert("RGB")

    out_w, out_h = FACEBOOK_FEED_WIDTH, FACEBOOK_FEED_HEIGHT

    # Already the target aspect (or wider): only resize if needed.
    if src.width >= src.height:
        if src.size == (out_w, out_h):
            return image_bytes
        fitted = Image.new("RGB", (out_w, out_h))
        scale = max(out_w / src.width, out_h / src.height)
        nw = max(1, int(round(src.width * scale)))
        nh = max(1, int(round(src.height * scale)))
        resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
        left = max(0, (nw - out_w) // 2)
        top = max(0, (nh - out_h) // 2)
        fitted.paste(resized.crop((left, top, left + out_w, top + out_h)), (0, 0))
        buf = BytesIO()
        fitted.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()

    # Scale so the card covers the square, then crop.
    scale = max(out_w / src.width, out_h / src.height)
    nw = max(1, int(round(src.width * scale)))
    nh = max(1, int(round(src.height * scale)))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)

    left = max(0, (nw - out_w) // 2)
    # Keep the bottom of the card (headline / CTAs live there on 4:5 templates).
    top = max(0, nh - out_h)
    cropped = resized.crop((left, top, left + out_w, top + out_h))

    buf = BytesIO()
    cropped.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()
