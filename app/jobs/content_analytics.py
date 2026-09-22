"""Scheduled content analytics refresh.

Run from the deployment scheduler, for example once per hour:
    python -m app.jobs.content_analytics
"""

import asyncio

from app.database import SessionLocal
from app.models.content_analytics import AnalyticsSyncRun, GaProperty
from app.models.gsc_property import GscProperty
from app.models.social_account import SocialAccount
from app.models.wordpress_site import WordPressSite
from app.services.content_analytics_sync_service import execute_sync_run


async def run_due_syncs() -> None:
    db = SessionLocal()
    try:
        user_ids = {
            row[0] for row in db.query(WordPressSite.user_id).distinct().all()
        } | {
            row[0] for row in db.query(GscProperty.user_id).distinct().all()
        } | {
            row[0] for row in db.query(SocialAccount.user_id).distinct().all()
        } | {
            row[0] for row in db.query(GaProperty.user_id).distinct().all()
        }
        runs: list[tuple[int, int]] = []
        for user_id in user_ids:
            active = db.query(AnalyticsSyncRun).filter(
                AnalyticsSyncRun.user_id == user_id,
                AnalyticsSyncRun.status.in_(["queued", "running"]),
            ).first()
            if active:
                if active.status == "queued":
                    runs.append((active.id, user_id))
                continue
            run = AnalyticsSyncRun(user_id=user_id, trigger="scheduled", scope="all", status="queued")
            db.add(run)
            db.flush()
            runs.append((run.id, user_id))
        db.commit()
    finally:
        db.close()
    for run_id, user_id in runs:
        await execute_sync_run(run_id, user_id)


if __name__ == "__main__":
    asyncio.run(run_due_syncs())
