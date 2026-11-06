"""Scheduler entry point for weekly auto-content (events social + CSV blogs).

Preferred free-tier schedule: GitHub Actions workflow `weekly-content.yml`
calls POST /api/weekly-content/cron/run-due every Monday 09:00 UTC.

Optional Render Cron Job (starter plan) can also run:

    python -m app.jobs.weekly_content

Idempotency is per user + ISO week (`weekly_content_runs`).
"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app.jobs.weekly_content")


async def _main() -> int:
    from app.services.weekly_content_service import run_due_weekly_content

    count = await run_due_weekly_content()
    logger.info("Ran weekly content for %s user(s)", count)
    print(f"Ran weekly content for {count} user(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception:
        logger.exception("Weekly content job failed")
        sys.exit(1)
