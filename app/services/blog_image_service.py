"""Auto-select and insert featured + section images for celebrity blog workflows."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.image_search_history import ImageSearchHistory
from app.models.workflow_state import WorkflowState, WorkflowStatus
from app.services.http_ssl import httpx_verify
from app.services.serpapi_service import search_images as serpapi_search_images

logger = logging.getLogger(__name__)

_IMAGE_FETCH_HEADERS = {"User-Agent": "SEO-Automation/1.0", "Accept": "image/*"}
_MIN_IMAGE_BYTES = 512

AutoContentType = str  # "biography" | "net_worth"

SECTION_HEADINGS: Dict[str, List[Tuple[str, List[str]]]] = {
    "biography": [
        ("career", ["Career Overview", "Professional Journey"]),
        ("personal_life", ["Personal Life"]),
        ("public_image", ["Public Image & Legacy", "Public Image"]),
    ],
    "net_worth": [
        ("career", ["Career & Achievements", "Career Earnings"]),
        ("personal_life", ["Personal Life"]),
    ],
}

# Preferred SerpAPI pool indices (0-based) for each pick in a bio+net-worth batch.
# Pattern: 1st featured, 3rd career, skip 4th, 5th personal, etc.
POSITIONAL_PICK_INDICES: List[int] = [0, 2, 4, 6, 1, 8, 10]

_IMG_SRC_PATTERN = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_MD_IMG_PATTERN = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')


def derive_featured_image_keyword(title: str) -> str:
    """Derive search keyword from blog title."""
    if not title or not title.strip():
        return "featured"
    t = title.strip()
    for prefix in ("Who Is ", "Who is ", "What Is ", "What is ", "Who Are ", "Who are "):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    for sep in ("?", ":", "–", "-"):
        if sep in t:
            t = t.split(sep)[0].strip()
    t = re.sub(r"\s+", " ", t)[:50].strip()
    return t or "featured"


def seo_alt_from_title(title: str) -> str:
    if not title or not title.strip():
        return "Featured image"
    t = re.sub(r"\s+", " ", title.strip())[:100]
    return t or "Featured image"


def collect_used_urls_from_body(body: str) -> Set[str]:
    urls: Set[str] = set()
    if not body:
        return urls
    for m in _IMG_SRC_PATTERN.finditer(body):
        urls.add(m.group(1).strip())
    for m in _MD_IMG_PATTERN.finditer(body):
        urls.add(m.group(1).strip())
    return urls


def add_image_urls_to_used(used_urls: Set[str], image: Dict[str, str]) -> None:
    for key in ("url", "original", "link", "thumbnail"):
        val = (image.get(key) or "").strip()
        if val:
            used_urls.add(val)


def resolve_celebrity_pool_keyword(focus: str, title: str) -> str:
    """Single SerpAPI search keyword: celebrity name only (no section suffix)."""
    return derive_featured_image_keyword((focus or title or "").strip())


def _normalize_image_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("//"):
        return "https:" + u
    return u


def _candidate_url_fields(candidate: Dict[str, Any]) -> List[str]:
    """Prefer thumbnail (hotlink-friendly), then original, then link."""
    seen: Set[str] = set()
    out: List[str] = []
    for key in ("thumbnail", "original", "link"):
        val = _normalize_image_url(str(candidate.get(key) or ""))
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _content_looks_like_image(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False


async def verify_image_url(url: str) -> bool:
    """Return True if URL responds with a reachable image (HEAD/GET probe)."""
    url = _normalize_image_url(url)
    if not url.startswith(("http://", "https://")):
        return False
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True, verify=httpx_verify()
        ) as client:
            resp = await client.head(url, headers=_IMAGE_FETCH_HEADERS)
            if resp.status_code == 405:
                resp = await client.get(
                    url,
                    headers={**_IMAGE_FETCH_HEADERS, "Range": "bytes=0-4095"},
                )
            elif resp.status_code != 200:
                resp = await client.get(
                    url,
                    headers={**_IMAGE_FETCH_HEADERS, "Range": "bytes=0-4095"},
                )

            if resp.status_code not in (200, 206):
                return False

            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if content_type.startswith("image/"):
                if resp.status_code == 206 or len(resp.content) >= _MIN_IMAGE_BYTES:
                    return True
                return len(resp.content) > 0

            body = resp.content
            if body and len(body) >= _MIN_IMAGE_BYTES and _content_looks_like_image(body):
                return True
    except Exception as e:
        logger.debug("Image URL verify failed for %s: %s", url, e)
    return False


async def resolve_verified_url(candidate: Dict[str, Any]) -> Optional[str]:
    """Return first working image URL from a SerpAPI candidate."""
    for url in _candidate_url_fields(candidate):
        if await verify_image_url(url):
            return url
    return None


def _resolve_display_url(candidate: Dict[str, Any]) -> Optional[str]:
    """Return first usable image URL without HTTP verification (thumbnail first)."""
    for url in _candidate_url_fields(candidate):
        if url.startswith(("http://", "https://")):
            return url
    return None


def _candidate_is_used(candidate: Dict[str, Any], used_urls: Set[str]) -> bool:
    urls = _candidate_url_fields(candidate)
    if not urls:
        return True
    return any(url in used_urls for url in urls)


def _mark_candidate_urls_used(used_urls: Set[str], candidate: Dict[str, Any]) -> None:
    for url in _candidate_url_fields(candidate):
        used_urls.add(url)


def _preferred_pool_index(pick_slot: int) -> int:
    if pick_slot < len(POSITIONAL_PICK_INDICES):
        return POSITIONAL_PICK_INDICES[pick_slot]
    return pick_slot


async def pick_from_pool(
    pool: List[Dict[str, Any]],
    used_urls: Set[str],
    context_title: str,
    context_meta: str = "",
    *,
    pick_slot: int = 0,
) -> Optional[Dict[str, str]]:
    """Pick one unused image from a pre-fetched pool using positional scheduling."""
    del context_meta  # kept for API compatibility; positional picks do not use LLM
    if not pool:
        return None

    preferred = _preferred_pool_index(pick_slot)
    for attempt in range(len(pool)):
        idx = (preferred + attempt) % len(pool)
        candidate = pool[idx]
        if _candidate_is_used(candidate, used_urls):
            continue
        display_url = _resolve_display_url(candidate)
        if not display_url:
            continue
        thumb = _normalize_image_url(str(candidate.get("thumbnail") or ""))
        result = {
            "url": display_url,
            "thumbnail": thumb or display_url,
            "title": (candidate.get("title") or "").strip(),
            "source": (candidate.get("source") or "").strip(),
            "alt": seo_alt_from_title(context_title),
        }
        _mark_candidate_urls_used(used_urls, candidate)
        add_image_urls_to_used(used_urls, result)
        return result

    return None


def insert_image_after_section(
    body: str,
    heading_patterns: List[str],
    img_url: str,
    alt: str,
) -> str:
    """Insert an HTML img tag after the first paragraph of a matching H2 section."""
    if not body or not img_url:
        return body
    lines = body.split("\n")
    lowered_patterns = [p.lower() for p in heading_patterns]
    img_line = f'<img src="{img_url}" alt="{alt}" class="max-w-full h-auto rounded-md my-4" />'

    for i, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        h2_text = line[3:].strip().lower()
        if not any(pat in h2_text for pat in lowered_patterns):
            continue

        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1

        if j >= len(lines) or lines[j].startswith("##"):
            insert_at = i + 1
        else:
            insert_at = j
            k = j
            while k < len(lines):
                if lines[k].startswith("##"):
                    insert_at = k
                    break
                if k > j and lines[k].strip() == "":
                    insert_at = k
                    break
                k += 1
            else:
                insert_at = len(lines)

        new_lines = lines[:insert_at] + ["", img_line, ""] + lines[insert_at:]
        return "\n".join(new_lines)

    return body


def _image_data_to_results(images_data: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    return [
        {
            "title": img.get("title", ""),
            "link": img.get("link", ""),
            "original": img.get("original", ""),
            "thumbnail": img.get("thumbnail", ""),
            "source": img.get("source", ""),
        }
        for img in images_data
    ]


def _get_images_from_history(db: Session, user_id: int, keyword: str) -> List[Dict[str, Any]]:
    row = (
        db.query(ImageSearchHistory)
        .filter(
            ImageSearchHistory.user_id == user_id,
            ImageSearchHistory.keyword == keyword,
        )
        .order_by(ImageSearchHistory.created_at.desc())
        .first()
    )
    if not row or not row.results:
        return []
    return list(row.results)


async def _fetch_image_pool(
    db: Session,
    user_id: int,
    keyword: str,
) -> List[Dict[str, Any]]:
    images_data = _get_images_from_history(db, user_id, keyword)
    if not images_data:
        try:
            from app.services.llm_settings_service import resolve_api_key
            serp_key = resolve_api_key(db, user_id, "serpapi")
            images_data = await serpapi_search_images(keyword, 100, api_key=serp_key)
        except Exception as e:
            logger.warning("Image search failed for keyword %s: %s", keyword, e)
            return []
        try:
            record = ImageSearchHistory(
                user_id=user_id,
                keyword=keyword,
                results=_image_data_to_results(images_data),
            )
            db.add(record)
            db.commit()
        except Exception as e:
            logger.warning("Could not save image search to history: %s", e)
            db.rollback()
    return images_data


def _pool_keyword_from_state(state_data: Dict[str, Any], blog_key: str = "parent") -> str:
    article = _resolve_article(state_data, blog_key)
    focus = (state_data.get("focus_keyword") or state_data.get("topic") or "").strip()
    title = (article.get("title") or state_data.get("title") or "").strip()
    return resolve_celebrity_pool_keyword(focus, title)


def _resolve_article(state_data: Dict[str, Any], blog_key: str = "parent") -> Dict[str, Any]:
    if state_data.get("is_tree") and blog_key in ("parent", "child_0", "child_1"):
        if blog_key == "parent":
            return state_data.get("parent") or {}
        idx = 0 if blog_key == "child_0" else 1
        children = state_data.get("children") or []
        return (children[idx] if idx < len(children) else None) or {}
    return state_data


def _persist_article(
    state_data: Dict[str, Any],
    blog_key: str,
    body_content: str,
    featured_image: Optional[Dict[str, str]],
) -> None:
    if state_data.get("is_tree") and blog_key in ("parent", "child_0", "child_1"):
        if blog_key == "parent":
            if "parent" not in state_data:
                state_data["parent"] = {}
            state_data["parent"]["body_content"] = body_content
            if featured_image:
                state_data["parent"]["featured_image"] = featured_image
        else:
            idx = 0 if blog_key == "child_0" else 1
            children = state_data.get("children") or []
            if idx < len(children):
                children[idx]["body_content"] = body_content
                if featured_image:
                    children[idx]["featured_image"] = featured_image
    else:
        state_data["body_content"] = body_content
        if featured_image:
            state_data["featured_image"] = featured_image


async def apply_auto_images_to_workflow(
    db: Session,
    workflow_state: WorkflowState,
    user_id: int,
    content_type: str,
    used_urls: Set[str],
    *,
    blog_key: str = "parent",
    exclude_urls: Optional[List[str]] = None,
    pool: Optional[List[Dict[str, Any]]] = None,
    pick_counter: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Pick featured + section images, insert into body, persist to workflow state."""
    if workflow_state.status != WorkflowStatus.COMPLETED:
        raise ValueError("Workflow must be completed before auto-images")

    if exclude_urls:
        for u in exclude_urls:
            if u and str(u).strip():
                used_urls.add(str(u).strip())

    state_data = workflow_state.state_data or {}
    article = _resolve_article(state_data, blog_key)
    body = (article.get("body_content") or state_data.get("body_content") or "").strip()
    title = (article.get("title") or state_data.get("title") or "").strip()
    meta = (article.get("meta_description") or "").strip()
    focus = (state_data.get("focus_keyword") or state_data.get("topic") or "").strip()

    used_urls.update(collect_used_urls_from_body(body))
    fi = article.get("featured_image") or state_data.get("featured_image")
    if isinstance(fi, dict):
        add_image_urls_to_used(used_urls, fi)

    section_images: List[Dict[str, str]] = []
    warnings: List[str] = []
    slot_counter = pick_counter if pick_counter is not None else [0]

    if pool is None:
        keyword = resolve_celebrity_pool_keyword(focus, title)
        pool = await _fetch_image_pool(db, user_id, keyword)
    if not pool:
        warnings.append("No images available from search pool")

    featured_image = None
    if pool:
        featured_image = await pick_from_pool(
            pool, used_urls, title, meta, pick_slot=slot_counter[0]
        )
        slot_counter[0] += 1
    if not featured_image:
        warnings.append("No unused featured image found")

    sections = SECTION_HEADINGS.get(content_type, [])
    for section_id, heading_patterns in sections:
        section_present = any(
            line.startswith("## ")
            and any(pat.lower() in line[3:].lower() for pat in heading_patterns)
            for line in body.split("\n")
        )
        if not section_present:
            warnings.append(f"Section not found: {section_id}")
            continue

        context = f"{title} - {heading_patterns[0]}"
        picked = None
        if pool:
            picked = await pick_from_pool(
                pool, used_urls, context, meta, pick_slot=slot_counter[0]
            )
            slot_counter[0] += 1
        if not picked:
            warnings.append(f"No image for section: {section_id}")
            continue

        body = insert_image_after_section(
            body,
            heading_patterns,
            picked["url"],
            picked.get("alt") or seo_alt_from_title(context),
        )
        section_images.append({
            "section": section_id,
            "url": picked["url"],
            "alt": picked.get("alt") or "",
        })

    _persist_article(state_data, blog_key, body, featured_image)
    workflow_state.state_data = state_data
    flag_modified(workflow_state, "state_data")
    db.commit()
    db.refresh(workflow_state)

    return {
        "workflow_id": workflow_state.id,
        "content_type": content_type,
        "featured_image": featured_image,
        "section_images": section_images,
        "used_urls": sorted(used_urls),
        "warnings": warnings,
    }


async def apply_auto_images_batch(
    db: Session,
    user_id: int,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Process multiple workflows with a shared used_urls pool (biography before net_worth)."""
    order = {"biography": 0, "net_worth": 1}
    sorted_items = sorted(
        items,
        key=lambda x: order.get(str(x.get("content_type", "")), 99),
    )
    used_urls: Set[str] = set()
    pick_counter: List[int] = [0]
    results: List[Dict[str, Any]] = []

    shared_pool: Optional[List[Dict[str, Any]]] = None
    for item in sorted_items:
        wf_id = int(item["workflow_id"])
        wf_state = (
            db.query(WorkflowState)
            .filter(WorkflowState.id == wf_id, WorkflowState.user_id == user_id)
            .first()
        )
        if wf_state:
            keyword = _pool_keyword_from_state(wf_state.state_data or {})
            shared_pool = await _fetch_image_pool(db, user_id, keyword)
            break

    for item in sorted_items:
        workflow_id = int(item["workflow_id"])
        content_type = str(item["content_type"])
        workflow_state = (
            db.query(WorkflowState)
            .filter(WorkflowState.id == workflow_id, WorkflowState.user_id == user_id)
            .first()
        )
        if not workflow_state:
            results.append({
                "workflow_id": workflow_id,
                "content_type": content_type,
                "error": "Workflow not found",
            })
            continue
        try:
            result = await apply_auto_images_to_workflow(
                db,
                workflow_state,
                user_id,
                content_type,
                used_urls,
                pool=shared_pool,
                pick_counter=pick_counter,
            )
            results.append(result)
        except Exception as e:
            logger.warning("auto-images failed for workflow %s: %s", workflow_id, e)
            results.append({
                "workflow_id": workflow_id,
                "content_type": content_type,
                "error": str(e),
                "warnings": [str(e)],
            })

    return {"results": results, "used_urls": sorted(used_urls)}
