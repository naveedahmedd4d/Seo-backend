"""Scheduler entry point for due Bot Automation workflows.

Run from the same cron/worker mechanism as the Reddit trends job, for example once
per minute. Idempotency keys prevent the same schedule slot from running twice.
"""

import asyncio

from app.services.bot_automation_service import run_due_bots


if __name__ == "__main__":
    count = asyncio.run(run_due_bots())
    print(f"Queued and ran {count} due bot(s)")
