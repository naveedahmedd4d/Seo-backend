import base64
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Request

from app.services.public_url import resolve_public_base_url

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "social"


def save_png_base64(image_base64: str) -> str:
    """Decode base64 PNG, save to disk, return filename."""
    raw = image_base64.strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    binary = base64.b64decode(raw)
    if not binary.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Only PNG images are supported for social post upload")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    path = UPLOAD_DIR / filename
    path.write_bytes(binary)
    return filename


def public_image_url(filename: str, request: Optional[Request] = None) -> str:
    base = resolve_public_base_url(request)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "", filename)
    return f"{base}/api/social-post/media/{safe_name}"


def resolve_media_path(filename: str) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "", filename)
    path = (UPLOAD_DIR / safe_name).resolve()
    if UPLOAD_DIR.resolve() not in path.parents and path != UPLOAD_DIR.resolve():
        raise FileNotFoundError("Invalid media path")
    if not path.is_file():
        raise FileNotFoundError("Media file not found")
    return path
