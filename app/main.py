import logging
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.config import settings
from app.database import engine, Base
from app.api import auth, wordpress, trending, research, images, blog, social_post, social_platforms, social_accounts, ai_humanizer
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
app.include_router(research.router, prefix="/api/research", tags=["research"])
app.include_router(images.router, prefix="/api/images", tags=["images"])
app.include_router(blog.router, prefix="/api/blog", tags=["blog"])
app.include_router(social_post.router, prefix="/api/social-post", tags=["social-post"])
app.include_router(social_platforms.router, prefix="/api/social-platforms", tags=["social-platforms"])
app.include_router(social_accounts.router, prefix="/api/social-accounts", tags=["social-accounts"])
app.include_router(ai_humanizer.router, prefix="/api/ai-humanizer", tags=["ai-humanizer"])


@app.on_event("startup")
def create_tables_if_missing():
    """Create any new tables (e.g. published_posts) without Alembic.
    Non-blocking: if the DB is unreachable the app still starts."""
    try:
        import app.models  # noqa: ensure all models registered
        from sqlalchemy import inspect, text
        # Quick connectivity check with short timeout
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                table.create(engine)
                logging.getLogger(__name__).info("Created missing table: %s", table.name)
    except Exception as e:
        logging.getLogger(__name__).warning("Startup table check skipped: %s", e)


@app.get("/")
async def root():
    return {"message": "SEO Automation Tool API"}


@app.get("/health")
async def health():
    return {"status": "healthy"}

