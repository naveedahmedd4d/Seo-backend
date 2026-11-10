"""Fetch and parse UN international days/weeks observances."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.services.http_ssl import httpx_verify
from app.services.international_events_service import _upsert_event

logger = logging.getLogger(__name__)

UN_LIST_URL = "https://www.un.org/en/observances/list-days-weeks"
SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "un_observances.json"
SOURCE = "un"


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return (slug or "un-observance")[:100]


def _absolute_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return UN_LIST_URL
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"https://www.un.org{href}"
    return href


def parse_un_observances_html(html: str) -> list[dict[str, Any]]:
    """Parse UN list-days-weeks HTML into month/day seed rows."""
    soup = BeautifulSoup(html or "", "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()

    for row in soup.select("div.views-row"):
        title_el = row.select_one(".views-field-title a")
        date_el = row.select_one('[property="dc:date"]')
        if not title_el or not date_el:
            continue
        title = title_el.get_text(" ", strip=True)
        if not title:
            continue
        content = date_el.get("content") or ""
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", content)
        if not match:
            continue
        _year, month, day = map(int, match.groups())
        external_id = _slug(title)
        key = (month, day, external_id)
        if key in seen:
            continue
        seen.add(key)
        kind = "week" if re.search(r"\bweek\b", title, re.I) else "day"
        items.append(
            {
                "month": month,
                "day": day,
                "title": title,
                "kind": kind,
                "url": _absolute_url(title_el.get("href") or ""),
                "external_id": external_id,
                "description": f"UN international {kind}",
            }
        )
    return items


def load_un_seed() -> list[dict[str, Any]]:
    if not SEED_PATH.exists():
        return []
    with SEED_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


async def fetch_un_observances() -> list[dict[str, Any]]:
    """Live-fetch the UN list page; fall back to checked-in seed on failure."""
    try:
        async with httpx.AsyncClient(
            timeout=45.0,
            verify=httpx_verify(),
            follow_redirects=True,
            headers={"User-Agent": "SeoBackend/1.0 (+weekly-social)"},
        ) as client:
            response = await client.get(UN_LIST_URL)
            response.raise_for_status()
            items = parse_un_observances_html(response.text)
            if items:
                return items
            logger.warning("UN observances page parsed 0 rows; using seed")
    except Exception as exc:
        logger.warning("UN observances fetch failed: %s", exc)
    return load_un_seed()


def sync_un_observances_for_years(
    db: Session,
    years: Iterable[int],
    items: Optional[list[dict[str, Any]]] = None,
) -> int:
    rows = items if items is not None else load_un_seed()
    count = 0
    for year in years:
        for item in rows:
            try:
                month = int(item["month"])
                day = int(item["day"])
                title = str(item["title"]).strip()
                # Include month/day so twice-yearly observances (e.g. World
                # Migratory Bird Day in May and October) do not share one key.
                base = item.get("external_id") or _slug(title)
                external_id = f"{base}-{month:02d}-{day:02d}-{year}"
                event_date = date(year, month, day)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping bad UN observance row %s: %s", item, exc)
                continue
            description = item.get("description") or "UN international observance"
            url = item.get("url")
            if url:
                description = f"{description}\n{url}"
            _upsert_event(
                db,
                event_date=event_date,
                title=title,
                country_or_scope="INT",
                source=SOURCE,
                external_id=external_id[:160],
                description=description,
            )
            count += 1
    db.commit()
    return count


async def sync_un_observances(db: Session, years: Iterable[int]) -> int:
    items = await fetch_un_observances()
    return sync_un_observances_for_years(db, years, items=items)
