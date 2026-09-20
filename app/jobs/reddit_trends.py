import asyncio

from app.services.reddit_trends_service import run_due_reddit_jobs


if __name__ == "__main__":
    count = asyncio.run(run_due_reddit_jobs())
    print(f"Started {count} Reddit trend job(s).")
