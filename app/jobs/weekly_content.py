"""Scheduler entry point for weekly auto-content (events social + CSV blogs).

Run from the deployment scheduler once per week, for example Monday 09:00 UTC:

    python -m app.jobs.weekly_content

Idempotency is per user + ISO week (`weekly_content_runs`).
"""

import asyncio

from app.services.weekly_content_service import run_due_weekly_content


if __name__ == "__main__":
    count = asyncio.run(run_due_weekly_content())
    print(f"Ran weekly content for {count} user(s)")
