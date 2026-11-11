import logging
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.config import settings
from app.database import engine, Base
from app.api import auth, wordpress, trending, reddit_trends, research, images, blog, social_post, social_platforms, social_accounts, ai_humanizer, search_console, authority, settings_llm, settings_blog, website_audit, bot_automation, content_analytics, google_analytics, weekly_content, news_topics, webhooks_claude_routine
from app.middleware import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# httpx's INFO request line includes the full URL. OAuth providers commonly
# accept secrets and tokens as query parameters, so never emit those URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(
    title="SEO Automation Tool API",
    description="Backend API for SEO automation tool",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(wordpress.router, prefix="/api/wordpress", tags=["wordpress"])
app.include_router(trending.router, prefix="/api/trending", tags=["trending"])
app.include_router(reddit_trends.router, prefix="/api/trending/reddit", tags=["reddit-trends"])
app.include_router(research.router, prefix="/api/research", tags=["research"])
app.include_router(images.router, prefix="/api/images", tags=["images"])
app.include_router(blog.router, prefix="/api/blog", tags=["blog"])
app.include_router(social_post.router, prefix="/api/social-post", tags=["social-post"])
app.include_router(social_platforms.router, prefix="/api/social-platforms", tags=["social-platforms"])
app.include_router(social_accounts.router, prefix="/api/social-accounts", tags=["social-accounts"])
app.include_router(ai_humanizer.router, prefix="/api/ai-humanizer", tags=["ai-humanizer"])
app.include_router(search_console.router, prefix="/api/search-console", tags=["search-console"])
app.include_router(authority.router, prefix="/api/authority", tags=["authority"])
app.include_router(settings_llm.router, prefix="/api/settings", tags=["settings"])
app.include_router(settings_blog.router, prefix="/api/settings", tags=["settings"])
app.include_router(website_audit.router, prefix="/api/website-audit", tags=["website-audit"])
app.include_router(bot_automation.router, prefix="/api/bot-automation", tags=["bot-automation"])
app.include_router(content_analytics.router, prefix="/api/content-analytics", tags=["content-analytics"])
app.include_router(google_analytics.router, prefix="/api/google-analytics", tags=["google-analytics"])
app.include_router(weekly_content.router, prefix="/api/weekly-content", tags=["weekly-content"])
app.include_router(news_topics.router, prefix="/api/news-topics", tags=["news-topics"])
app.include_router(
    webhooks_claude_routine.router,
    prefix="/api/webhooks",
    tags=["webhooks"],
)


@app.on_event("startup")
def create_tables_if_missing():
    """Create missing tables/columns when Alembic was not run on deploy.
    Non-blocking: if the DB is unreachable the app still starts."""
    log = logging.getLogger(__name__)
    try:
        import app.models  # noqa: ensure all models registered
        from sqlalchemy import inspect, text

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        # Prefer Alembic when available (adds columns + stamps revision).
        try:
            from alembic import command
            from alembic.config import Config

            cfg = Config("alembic.ini")
            command.upgrade(cfg, "head")
            log.info("Alembic upgrade head completed on startup")
        except Exception as alembic_err:
            log.warning("Alembic startup upgrade skipped: %s", alembic_err)

        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        alembic_managed_prefixes = ("authority_",)
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                if table.name.startswith(alembic_managed_prefixes):
                    log.info("Skipping startup create for Alembic-managed table: %s", table.name)
                    continue
                table.create(engine)
                log.info("Created missing table: %s", table.name)
                continue

            # Backfill columns on existing tables (e.g. user_llm_settings expansions).
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                ddl = (
                    f'ALTER TABLE "{table.name}" '
                    f'ADD COLUMN IF NOT EXISTS "{column.name}" {col_type}'
                )
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                log.info("Added missing column %s.%s", table.name, column.name)
                existing_cols.add(column.name)
    except Exception as e:
        log.warning("Startup table check skipped: %s", e)


@app.get("/")
async def root():
    return {"message": "SEO Automation Tool API"}


@app.get("/health")
async def health():
    """Liveness plus the commit actually running.

    Render sets RENDER_GIT_COMMIT on every build. Without it there is no way
    to tell a deploy that has not rolled from a fix that did not work, which
    is otherwise only answerable by guessing.
    """
    import os

    return {
        "status": "healthy",
        "commit": (os.environ.get("RENDER_GIT_COMMIT") or "unknown")[:7],
    }

