"""Options for the "published article" picker.

published_posts only records what *this app* published. A site connected with
existing content has none, so a user with four live articles sees an empty
dropdown and a message telling them to publish one -- which they already did.

So the picker is sourced from both: tracked rows (which carry the workflow's
own metadata, like the target keyword) merged with whatever the site reports
as published. A post present in both appears once, with the tracked row
winning because it knows more about the article.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _label(title: Optional[str]) -> str:
    return (title or "").strip() or "Untitled article"


def merge_options(
    tracked: List[Dict[str, Any]],
    live: List[Dict[str, Any]],
    *,
    site_id: int,
    website: str,
) -> List[Dict[str, Any]]:
    """Tracked rows first, then live posts this app did not publish.

    Keyed on wp_post_id, which is the site's own identifier and the only thing
    both sources agree on; `id` (the published_posts row) is None for a post
    that exists only on the site.
    """
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()

    for row in tracked:
        wp_id = row.get("wp_post_id")
        if wp_id is not None:
            seen.add(wp_id)
        out.append({**row, "title": _label(row.get("title"))})

    for row in live:
        wp_id = row.get("wp_post_id")
        if wp_id is None or wp_id in seen:
            continue
        seen.add(wp_id)
        out.append({
            "id": None,
            "wp_post_id": wp_id,
            "site_id": site_id,
            "title": _label(row.get("title")),
            "website": website,
            "url": row.get("url"),
            "published_at": row.get("modified_gmt"),
        })

    return out
