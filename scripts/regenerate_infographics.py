"""Regenerate the auto infographics of an existing Technology & AI blog.

Removes the article's current infographics, runs the infographic pipeline again on the
saved article, and stores the new body (and featured image, when it was an infographic).

    python scripts/regenerate_infographics.py --list            # recent Technology & AI blogs
    python scripts/regenerate_infographics.py --latest          # the most recent one
    python scripts/regenerate_infographics.py --id 372          # a specific blog (workflow id)
    python scripts/regenerate_infographics.py --id 372 --dry-run

Needs FRONTEND_URL and INFOGRAPHIC_RENDER_SECRET in backend/.env, and the frontend
(which renders the images) running at FRONTEND_URL. Already-published WordPress posts
are not changed; republish the blog to push the new images.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.database import SessionLocal
from app.models.published_post import PublishedPost
from app.models.workflow_state import WorkflowState, WorkflowStatus
from app.services.openrouter_service import _blog_delay, generate_blog_seo_content
from app.services.technology_ai_content import TECHNOLOGY_AI_CATEGORY, content_fingerprint
from app.services.technology_ai_infographics import add_infographics_to_article

# An infographic line as inserted by the pipeline: <img src=".../api/blog/infographics/x.png" ... />
_INFOGRAPHIC_LINE = re.compile(r'^[ \t]*<img [^>]*src="[^"]*/api/blog/infographics/[^"]+"[^>]*/?>[ \t]*$', re.M)


def strip_infographics(body: str) -> tuple[str, int]:
    body, removed = _INFOGRAPHIC_LINE.subn("", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n", removed


def is_infographic_url(url: str | None) -> bool:
    return bool(url) and "/api/blog/infographics/" in url


def technology_blogs(db, limit: int):
    rows = (
        db.query(WorkflowState)
        .filter(WorkflowState.status == WorkflowStatus.COMPLETED)
        .order_by(WorkflowState.id.desc())
        .limit(limit * 10)
        .all()
    )
    return [
        w for w in rows
        if isinstance(w.state_data, dict)
        and w.state_data.get("category") == TECHNOLOGY_AI_CATEGORY
        and not w.state_data.get("is_tree")
        and w.state_data.get("body_content")
    ][:limit]


def list_blogs(db) -> None:
    blogs = technology_blogs(db, 15)
    if not blogs:
        print("No completed Technology & AI blogs found.")
        return
    print("id\tcreated\t\t\tinfographics\ttitle")
    for w in blogs:
        count = len(_INFOGRAPHIC_LINE.findall(w.state_data["body_content"]))
        created = w.created_at.strftime("%Y-%m-%d %H:%M") if w.created_at else "-"
        print(f"{w.id}\t{created}\t{count}\t\t{w.state_data.get('title') or w.state_data.get('topic')}")


async def regenerate(db, workflow: WorkflowState, dry_run: bool) -> None:
    data = dict(workflow.state_data)
    title = data.get("title") or data.get("topic") or ""
    body, removed = strip_infographics(data["body_content"])
    print(f"Blog {workflow.id}: {title}")
    print(f"  Removed {removed} existing infographic(s). Generating new ones (this takes about 20-60s)...")

    new_body, warnings, featured = await add_infographics_to_article(
        data.get("topic") or title, body, generate_blog_seo_content, _blog_delay, source_title=title,
    )
    urls = re.findall(r'src="([^"]*/api/blog/infographics/[^"]+)"', new_body)
    print(f"  Generated {len(urls)} infographic(s):")
    for url in urls:
        print(f"    {url}")
    for warning in warnings:
        print(f"  Note: {warning}")
    if not urls:
        print("  Nothing was generated; the blog was left unchanged.")
        return
    if dry_run:
        print("  Dry run: the images were created, but the blog was not changed.")
        return

    data["body_content"] = new_body
    current = (data.get("featured_image") or {}).get("url")
    if featured and (not current or is_infographic_url(current)):
        data["featured_image"] = featured  # keep a featured image the user picked themselves
    if isinstance(data.get("quality_review"), dict):
        # Swapping images isn't an editorial change; keep the review valid for the new body.
        data["quality_review"] = {**data["quality_review"], "body_fingerprint": content_fingerprint(new_body)}
    workflow.state_data = data
    flag_modified(workflow, "state_data")
    db.commit()
    print("  Saved. Open the blog in the dashboard to see the new infographics.")
    if db.query(PublishedPost).filter(PublishedPost.workflow_id == workflow.id).first():
        print("  This blog is already published to WordPress: republish it to update the live post.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate infographics for a Technology & AI blog")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--list", action="store_true", help="List recent Technology & AI blogs")
    target.add_argument("--latest", action="store_true", help="Use the most recent Technology & AI blog")
    target.add_argument("--id", type=int, help="Blog (workflow) id, as shown by --list")
    parser.add_argument("--dry-run", action="store_true", help="Generate images without changing the blog")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            list_blogs(db)
            return
        if not settings.frontend_url or not settings.infographic_render_secret:
            raise SystemExit("Set FRONTEND_URL and INFOGRAPHIC_RENDER_SECRET in backend/.env first.")
        if args.latest:
            blogs = technology_blogs(db, 1)
            if not blogs:
                raise SystemExit("No completed Technology & AI blogs found.")
            workflow = blogs[0]
        else:
            workflow = db.query(WorkflowState).filter(WorkflowState.id == args.id).first()
            if not workflow or not isinstance(workflow.state_data, dict):
                raise SystemExit(f"Blog {args.id} not found.")
            if workflow.state_data.get("category") != TECHNOLOGY_AI_CATEGORY:
                raise SystemExit(f"Blog {args.id} is not a Technology & AI blog; infographics are only for that category.")
            if workflow.state_data.get("is_tree") or not workflow.state_data.get("body_content"):
                raise SystemExit(f"Blog {args.id} has no single article body to add infographics to.")
        asyncio.run(regenerate(db, workflow, args.dry_run))
    finally:
        db.close()


if __name__ == "__main__":
    main()
