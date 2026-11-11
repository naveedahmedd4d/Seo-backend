"""Scheduler entry for the daily news blog.

The hourly GitHub Actions workflow calls POST /api/news-topics/cron/run-due.
Users with the daily blog turned on publish one post. A user who already
published today is skipped.

    python -m app.jobs.news_topics
"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app.jobs.news_topics")


async def _main() -> int:
    from app.services.news_topics_service import run_due_news_topics

    count = await run_due_news_topics()
    logger.info("Ran news topics for %s user(s)", count)
    print(f"Ran news topics for {count} user(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception:
        logger.exception("News topics job failed")
        sys.exit(1)
