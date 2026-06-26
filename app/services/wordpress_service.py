import re
import httpx
import markdown
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from app.models.wordpress_site import WordPressSite
from app.config import settings
import base64
import hashlib
import logging

logger = logging.getLogger(__name__)


def _slugify_anchor_id(text: str) -> str:
    """Slugify heading text for anchor IDs. Matches frontend slugifyAnchorId."""
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"(^-|-$)+", "", s)
    return s or "heading"


def _add_heading_ids(html: str) -> str:
    """Add id attributes to h1-h6 so ToC anchor links work in WordPress."""
    def repl(match: re.Match) -> str:
        level, attrs, content = match.group(1), match.group(2), match.group(3)
        text = re.sub(r"<[^>]+>", "", content)
        slug = _slugify_anchor_id(text)
        attrs = re.sub(r'\s*id\s*=\s*["\'][^"\']*["\']', "", attrs).strip()
        attr_space = f" {attrs}" if attrs else ""
        return f"<{level}{attr_space} id=\"{slug}\">{content}</{level}>"
    return re.sub(
        r"<(h[1-6])([^>]*)>(.*?)</\1>",
        repl,
        html,
        flags=re.DOTALL,
    )


def _wrap_tables(html: str) -> str:
    """Wrap tables in same wrapper as frontend for consistent styling."""
    html = re.sub(
        r"<table(\s[^>]*)?>",
        r'<div class="blog-table-wrap relative overflow-auto"><table\1>',
        html,
    )
    return html.replace("</table>", "</table></div>")


def _markdown_to_html(content: str) -> str:
    """Convert markdown content to HTML before sending to WordPress.

    - If content is already HTML (block tags present), return unchanged to avoid double-conversion.
    - Otherwise always convert so that headings, lists, bold, tables, and links from the editor
      are sent as proper HTML and display with the same structure on WordPress.
    """
    if not content or not content.strip():
        return content

    stripped = content.strip()
    # Already HTML: avoid double-conversion (e.g. if someone pasted HTML or we ever send HTML)
    if stripped.startswith("<") or re.search(
        r"<\s*(?:p|div|h[1-6]|ul|ol|li|table|thead|tbody|tr|th|td)\s",
        content,
    ):
        return content

    # Python-Markdown requires a blank line before a list to output <ul>/<ol>. Without it,
    # "* item" can be parsed as emphasis. Insert a blank line before any list-item line
    # that immediately follows a non-blank line so bullets render as proper HTML lists.
    content = re.sub(
        r"([^\n])\n(\s*[*+-]\s+)",
        r"\1\n\n\2",
        content,
    )

    try:
        logger.info("Converting markdown to HTML for WordPress publish")
        html = markdown.markdown(content, extensions=["tables", "nl2br"])
        html = _add_heading_ids(html)
        html = _wrap_tables(html)
        return html
    except Exception as e:
        logger.warning("Markdown conversion failed, using raw content: %s", e)
        return content

# Generate a key for encryption (in production, this should be stored securely)
# For now, we'll derive it from the secret key
def get_encryption_key() -> bytes:
    """Get encryption key from secret key"""
    key = hashlib.sha256(settings.secret_key.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_api_key(api_key: str) -> str:
    """Encrypt API key for storage"""
    f = Fernet(get_encryption_key())
    return f.encrypt(api_key.encode()).decode()


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt API key for use"""
    f = Fernet(get_encryption_key())
    return f.decrypt(encrypted_key.encode()).decode()


async def test_wordpress_connection(url: str, username: str, api_key: str) -> Dict[str, Any]:
    """Test WordPress REST API connection"""
    try:
        # Normalize URL
        if not url.startswith(('http://', 'https://')):
            url = f"https://{url}"
        url = url.rstrip('/')
        
        # WordPress REST API endpoint for testing
        test_url = f"{url}/wp-json/wp/v2/users/me"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                test_url,
                auth=(username, api_key),
            )
            
            if response.status_code == 200:
                user_data = response.json()
                return {
                    "success": True,
                    "message": "Connection successful",
                    "user": user_data.get("name", "Unknown")
                }
            else:
                return {
                    "success": False,
                    "message": f"Connection failed: {response.status_code} - {response.text}"
                }
    except httpx.TimeoutException:
        return {
            "success": False,
            "message": "Connection timeout"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Connection error: {str(e)}"
        }


async def _fetch_image_from_url(image_url: str) -> Optional[Tuple[bytes, str, str]]:
    """Fetch image from URL. Returns (bytes, content_type, filename) or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                image_url,
                headers={"User-Agent": "SEO-Automation/1.0"},
            )
            if resp.status_code != 200:
                return None
            content_type = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                content_type = "image/jpeg"
            path = urlparse(image_url).path
            filename = path.split("/")[-1] if path else "image.jpg"
            if not filename or "." not in filename:
                ext = {"image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(content_type, ".jpg")
                filename = f"featured{ext}"
            return (resp.content, content_type, filename)
    except Exception as e:
        logger.warning("Failed to fetch featured image from URL %s: %s", image_url, e)
        return None


async def _upload_media_to_wordpress(
    site: WordPressSite,
    client: httpx.AsyncClient,
    image_bytes: bytes,
    content_type: str,
    filename: str,
) -> Optional[Tuple[int, str]]:
    """Upload image to WordPress media library. Returns (attachment id, source_url) or None."""
    try:
        url = site.url.rstrip("/")
        media_url = f"{url}/wp-json/wp/v2/media"
        decrypted_key = decrypt_api_key(site.api_key)
        response = await client.post(
            media_url,
            auth=(site.username, decrypted_key),
            files={"file": (filename, image_bytes, content_type)},
        )
        if response.status_code in (200, 201):
            data = response.json()
            mid = data.get("id")
            src = data.get("source_url") or data.get("link") or ""
            if not src and isinstance(data.get("guid"), dict):
                src = (data.get("guid") or {}).get("rendered") or ""
            if mid is not None:
                return (mid, src if isinstance(src, str) else "")
        logger.warning("WordPress media upload failed: %s - %s", response.status_code, response.text)
        return None
    except Exception as e:
        logger.warning("WordPress media upload error: %s", e)
        return None


MAX_INLINE_BODY_IMAGES = 40


def _site_hostname(site: WordPressSite) -> str:
    u = (site.url or "").strip()
    if not u.startswith(("http://", "https://")):
        u = f"https://{u}"
    return urlparse(u.rstrip("/")).netloc.lower()


def _normalize_image_fetch_url(img_url: str) -> str:
    u = (img_url or "").strip()
    if u.startswith("//"):
        return "https:" + u
    return u


def _should_skip_body_image_sideload(img_url: str, site_host: str) -> bool:
    img_url = _normalize_image_fetch_url(img_url)
    if not img_url or img_url.lower().startswith("data:"):
        return True
    p = urlparse(img_url)
    if p.scheme not in ("http", "https"):
        return True
    if p.netloc.lower() == site_host:
        return True
    return False


def _replace_img_src_url(html: str, old_url: str, new_url: str) -> str:
    """Replace src in <img> tags only, preserving quote style.

    Handles multiline <img> (e.g. <img newline src="...") — patterns must not require
    a single \\s immediately before ``src`` after <img>.
    """
    esc = re.escape(old_url)
    # From <img through the opening quote of src; then old URL; then closing quote (same as opening)
    pattern = r"(<img\b[\s\S]*?)(src\s*=\s*)([\"'])" + r"(" + esc + r")(\3)"
    return re.sub(
        pattern,
        lambda m: m.group(1) + m.group(2) + m.group(3) + new_url + m.group(5),
        html,
        flags=re.IGNORECASE,
    )


async def _sideload_inline_images_in_html(
    site: WordPressSite,
    client: httpx.AsyncClient,
    html: str,
) -> Tuple[str, Dict[str, int]]:
    """Fetch remote <img src> URLs, upload to WP Media, replace src with source_url.

    Returns (updated_html, map of original_image_url -> media_id) for featured-image dedup.
    """
    if not html or "<img" not in html.lower():
        return html, {}

    site_host = _site_hostname(site)
    urls_in_order: List[str] = []
    seen: set = set()
    for m in re.finditer(r"<img\b[^>]+>", html, re.IGNORECASE | re.DOTALL):
        tag = m.group(0)
        # src may be on a new line after <img; do not require leading \\s at tag start
        sm = re.search(r"src\s*=\s*([\"'])([^\"']+)\1", tag, re.IGNORECASE)
        if not sm:
            continue
        raw_src = sm.group(2).strip()
        if _should_skip_body_image_sideload(raw_src, site_host):
            continue
        if raw_src not in seen:
            seen.add(raw_src)
            urls_in_order.append(raw_src)

    if not urls_in_order:
        return html, {}

    if len(urls_in_order) > MAX_INLINE_BODY_IMAGES:
        logger.warning(
            "Sideload: limiting inline images from %s to %s",
            len(urls_in_order),
            MAX_INLINE_BODY_IMAGES,
        )
        urls_in_order = urls_in_order[:MAX_INLINE_BODY_IMAGES]

    url_to_media_id: Dict[str, int] = {}
    out = html

    for u in urls_in_order:
        fetch_url = _normalize_image_fetch_url(u)
        fetched = await _fetch_image_from_url(fetch_url)
        if not fetched:
            logger.warning("Sideload body image: fetch failed for %s", u)
            continue
        image_bytes, content_type, filename = fetched
        up = await _upload_media_to_wordpress(site, client, image_bytes, content_type, filename)
        if not up:
            logger.warning("Sideload body image: upload failed for %s", u)
            continue
        mid, src_url = up
        if not src_url:
            logger.warning("Sideload body image: no source_url for media id %s", mid)
            continue
        url_to_media_id[u] = mid
        out = _replace_img_src_url(out, u, src_url)

    return out, url_to_media_id


def _rank_math_meta(
    focus_keyword: Optional[str] = None,
    seo_title: Optional[str] = None,
    meta_description: Optional[str] = None,
) -> Dict[str, str]:
    """Build Rank Math meta dict for REST API (only non-empty values)."""
    meta: Dict[str, str] = {}
    if focus_keyword and str(focus_keyword).strip():
        meta["rank_math_focus_keyword"] = str(focus_keyword).strip()
    if seo_title and str(seo_title).strip():
        meta["rank_math_title"] = str(seo_title).strip()
    if meta_description and str(meta_description).strip():
        meta["rank_math_description"] = str(meta_description).strip()
    return meta


async def publish_page_to_wordpress(
    site: WordPressSite,
    title: str,
    content: str,
    status: str = "draft",
    featured_image: Any = None,
    content_is_html: bool = False,
    focus_keyword: Optional[str] = None,
    slug: Optional[str] = None,
    seo_title: Optional[str] = None,
    meta_description: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish content to WordPress as a Page (wp/v2/pages)."""
    try:
        url = site.url.rstrip("/")
        api_url = f"{url}/wp-json/wp/v2/pages"
        decrypted_key = decrypt_api_key(site.api_key)

        if not content_is_html:
            content = _markdown_to_html(content)

        async with httpx.AsyncClient(timeout=120.0) as client:
            content, sideload_url_to_media_id = await _sideload_inline_images_in_html(site, client, content)

            featured_media_id: Optional[int] = None
            image_url = None
            if featured_image and isinstance(featured_image, dict):
                image_url = featured_image.get("url") or featured_image.get("original")
            if image_url and isinstance(image_url, str) and image_url.strip():
                iu = image_url.strip()
                if iu in sideload_url_to_media_id:
                    featured_media_id = sideload_url_to_media_id[iu]
                else:
                    fetched = await _fetch_image_from_url(iu)
                    if fetched:
                        image_bytes, content_type, filename = fetched
                        up = await _upload_media_to_wordpress(
                            site, client, image_bytes, content_type, filename
                        )
                        if up:
                            featured_media_id = up[0]

            page_payload: Dict[str, Any] = {
                "title": title,
                "content": content,
                "status": status,
            }
            if featured_media_id is not None:
                page_payload["featured_media"] = featured_media_id
            if slug and str(slug).strip():
                page_payload["slug"] = str(slug).strip()
            meta = _rank_math_meta(focus_keyword, seo_title, meta_description)
            if meta:
                page_payload["meta"] = meta
            response = await client.post(
                api_url,
                auth=(site.username, decrypted_key),
                json=page_payload,
            )

        if response.status_code == 201:
            page_data = response.json()
            return {
                "success": True,
                "page_id": page_data.get("id"),
                "link": page_data.get("link"),
                "message": "Page published successfully",
            }
        return {
            "success": False,
            "message": f"Failed to publish page: {response.status_code} - {response.text}",
        }
    except Exception as e:
        return {"success": False, "message": f"Error publishing page: {str(e)}"}


async def delete_page_from_wordpress(site: WordPressSite, wp_page_id: int) -> Dict[str, Any]:
    """Delete a page from WordPress via REST API."""
    try:
        url = site.url.rstrip("/")
        api_url = f"{url}/wp-json/wp/v2/pages/{wp_page_id}"
        decrypted_key = decrypt_api_key(site.api_key)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                f"{api_url}?force=true",
                auth=(site.username, decrypted_key),
            )
            if response.status_code in (200, 204):
                return {"success": True, "message": "Page deleted from WordPress"}
            return {
                "success": False,
                "message": f"Failed to delete page: {response.status_code} - {response.text}",
            }
    except Exception as e:
        return {"success": False, "message": f"Error deleting page: {str(e)}"}


async def publish_to_wordpress(
    site: WordPressSite,
    title: str,
    content: str,
    status: str = "draft",
    featured_image: Any = None,
    content_is_html: bool = False,
    focus_keyword: Optional[str] = None,
    slug: Optional[str] = None,
    seo_title: Optional[str] = None,
    meta_description: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish content to WordPress. featured_image is optional; if provided (dict with 'url'),
    the image is fetched, uploaded to the media library, and set as the post's featured image.
    Remote <img> tags in post content are uploaded to the media library and src URLs are rewritten.
    focus_keyword, seo_title, meta_description are sent as Rank Math post meta. slug sets the post URL slug."""
    try:
        url = site.url.rstrip("/")
        api_url = f"{url}/wp-json/wp/v2/posts"
        decrypted_key = decrypt_api_key(site.api_key)

        if not content_is_html:
            content = _markdown_to_html(content)

        async with httpx.AsyncClient(timeout=120.0) as client:
            content, sideload_url_to_media_id = await _sideload_inline_images_in_html(site, client, content)

            featured_media_id: Optional[int] = None
            image_url = None
            if featured_image and isinstance(featured_image, dict):
                image_url = featured_image.get("url") or featured_image.get("original")
            if image_url and isinstance(image_url, str) and image_url.strip():
                iu = image_url.strip()
                if iu in sideload_url_to_media_id:
                    featured_media_id = sideload_url_to_media_id[iu]
                else:
                    fetched = await _fetch_image_from_url(iu)
                    if fetched:
                        image_bytes, content_type, filename = fetched
                        up = await _upload_media_to_wordpress(
                            site, client, image_bytes, content_type, filename
                        )
                        if up:
                            featured_media_id = up[0]

            post_payload: Dict[str, Any] = {
                "title": title,
                "content": content,
                "status": status,
            }
            if featured_media_id is not None:
                post_payload["featured_media"] = featured_media_id
            if slug and str(slug).strip():
                post_payload["slug"] = str(slug).strip()
            meta = _rank_math_meta(focus_keyword, seo_title, meta_description)
            if meta:
                post_payload["meta"] = meta
            response = await client.post(
                api_url,
                auth=(site.username, decrypted_key),
                json=post_payload,
            )

        if response.status_code == 201:
            post_data = response.json()
            return {
                "success": True,
                "post_id": post_data.get("id"),
                "link": post_data.get("link"),
                "message": "Post published successfully"
            }
        else:
            return {
                "success": False,
                "message": f"Failed to publish: {response.status_code} - {response.text}"
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error publishing: {str(e)}"
        }


async def delete_post_from_wordpress(site: WordPressSite, wp_post_id: int) -> Dict[str, Any]:
    """Delete a post from WordPress via REST API (permanent delete with force=true)."""
    try:
        url = site.url.rstrip("/")
        api_url = f"{url}/wp-json/wp/v2/posts/{wp_post_id}"
        decrypted_key = decrypt_api_key(site.api_key)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                f"{api_url}?force=true",
                auth=(site.username, decrypted_key),
            )
            if response.status_code in (200, 204):
                return {"success": True, "message": "Post deleted from WordPress"}
            return {
                "success": False,
                "message": f"Failed to delete: {response.status_code} - {response.text}",
            }
    except Exception as e:
        return {"success": False, "message": f"Error deleting post: {str(e)}"}


def get_wordpress_site(db: Session, site_id: int, user_id: int) -> Optional[WordPressSite]:
    """Get WordPress site by ID for a user"""
    return db.query(WordPressSite).filter(
        WordPressSite.id == site_id,
        WordPressSite.user_id == user_id
    ).first()


def get_user_wordpress_sites(db: Session, user_id: int) -> list[WordPressSite]:
    """Get all WordPress sites for a user"""
    return db.query(WordPressSite).filter(WordPressSite.user_id == user_id).all()


def create_wordpress_site(
    db: Session,
    user_id: int,
    name: str,
    url: str,
    username: str,
    api_key: str
) -> WordPressSite:
    """Create a new WordPress site"""
    encrypted_key = encrypt_api_key(api_key)
    site = WordPressSite(
        user_id=user_id,
        name=name,
        url=url,
        username=username,
        api_key=encrypted_key
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


def update_wordpress_site(
    db: Session,
    site: WordPressSite,
    name: Optional[str] = None,
    url: Optional[str] = None,
    username: Optional[str] = None,
    api_key: Optional[str] = None
) -> WordPressSite:
    """Update WordPress site"""
    if name:
        site.name = name
    if url:
        site.url = url
    if username:
        site.username = username
    if api_key:
        site.api_key = encrypt_api_key(api_key)
    db.commit()
    db.refresh(site)
    return site


def delete_wordpress_site(db: Session, site: WordPressSite) -> None:
    """Delete WordPress site"""
    db.delete(site)
    db.commit()

