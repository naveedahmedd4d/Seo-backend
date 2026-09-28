"""Local storage for auto-generated Technology & AI infographic images."""

import base64
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Request

from app.services.public_url import resolve_public_base_url

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "infographics"


def save_png_base64(image_base64: str, slug: str = "") -> str:
    """Decode base64 PNG, save to disk, return filename.

    A descriptive slug becomes the filename prefix; WordPress keeps the name when it
    sideloads the image, which helps image search.
    """
    raw = image_base64.strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    binary = base64.b64decode(raw)
    if not binary.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Only PNG images are supported for infographic storage")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_slug = re.sub(r"[^a-z0-9-]", "", slug.lower())[:60].strip("-")
    unique = uuid.uuid4().hex[:10]
    filename = f"{safe_slug}-{unique}.png" if safe_slug else f"{uuid.uuid4().hex}.png"
    path = UPLOAD_DIR / filename
    path.write_bytes(binary)
    return filename


def public_infographic_url(filename: str, request: Optional[Request] = None) -> str:
    base = resolve_public_base_url(request)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "", filename)
    return f"{base}/api/blog/infographics/{safe_name}"


def resolve_infographic_path(filename: str) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "", filename)
    path = (UPLOAD_DIR / safe_name).resolve()
    if UPLOAD_DIR.resolve() not in path.parents and path != UPLOAD_DIR.resolve():
        raise FileNotFoundError("Invalid media path")
    if not path.is_file():
        raise FileNotFoundError("Media file not found")
    return path
