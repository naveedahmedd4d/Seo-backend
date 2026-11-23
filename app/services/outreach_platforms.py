"""Canonical set of article / backlink outreach platforms.

Social image platforms stay separate (Facebook, Instagram, X, …).
Anything in OUTREACH_PLATFORMS is shown on the Backlinks route and can
receive long-form republishes with a crawlable first-party backlink.
"""

from __future__ import annotations

OUTREACH_PLATFORMS: frozenset[str] = frozenset(
    {
        "devto",
        "hashnode",
        "blogger",
        "tumblr",
        "wordpress_com",
        "telegraph",
    }
)


def is_outreach_platform(platform: str | None) -> bool:
    if not platform:
        return False
    return platform.strip().lower() in OUTREACH_PLATFORMS
