"""Configuration for Blog Content Review audits."""

from __future__ import annotations

from typing import Literal

ArticleType = Literal["biography", "net_worth", "news", "cluster_hub", "unknown"]

DEFAULT_POST_LIMIT = 50
HARD_MAX_POSTS = 200
FETCH_CONCURRENCY = 3
CHUNK_SIZE = 5
CHUNK_PAUSE_SECONDS = 0.75
REQUEST_GAP_SECONDS = 0.35

# Audit word targets (TrendifyPress policy — net_worth audit uses 2500)
WORD_TARGETS: dict[str, int] = {
    "biography": 2500,
    "net_worth": 2500,
    "news": 800,
    "cluster_hub": 1500,
    "unknown": 1200,
}

# Weighted overall score
SCORE_WEIGHTS: dict[str, float] = {
    "seo": 0.12,
    "content_quality": 0.15,
    "search_intent": 0.13,
    "content_depth": 0.18,
    "originality": 0.12,
    "eeat": 0.15,
    "adsense_quality": 0.15,
}

LLM_SCORE_ADJUSTMENT_MAX = 15

NET_WORTH_BOILERPLATE_PHRASES = (
    "net worth is estimated",
    "estimated net worth",
    "according to various sources",
    "sources estimate",
    "early life and career",
    "personal life",
    "real estate holdings",
    "business ventures",
    "salary and earnings",
)

UNSUPPORTED_SUPERLATIVE_PATTERNS = (
    "richest in the world",
    "highest paid",
    "most successful ever",
    "billionaire status confirmed",
)

SOURCE_INDICATORS = (
    "wikipedia",
    "according to",
    "sources:",
    "last updated",
    "reported by",
    "as of 20",
)
