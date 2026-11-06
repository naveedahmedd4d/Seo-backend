"""Sync international observances and Nager.Date public holidays."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.weekly_content import CalendarEvent
from app.services.http_ssl import httpx_verify

logger = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "international_observances.json"
DEFAULT_COUNTRIES = ["US", "GB", "PK"]
NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"


def _load_seed() -> list[dict]:
    if not SEED_PATH.exists():
        logger.warning("Observances seed missing at %s", SEED_PATH)
        return []
    with SEED_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


def _upsert_event(
    db: Session,
    *,
    event_date: date,
    title: str,
    country_or_scope: str,
    source: str,
    external_id: str,
    description: Optional[str] = None,
) -> CalendarEvent:
    row = (
        db.query(CalendarEvent)
        .filter(CalendarEvent.external_id == external_id, CalendarEvent.source == source)
        .first()
    )
    if row:
        row.event_date = event_date
        row.title = title
        row.country_or_scope = country_or_scope
        row.description = description
        return row
    row = CalendarEvent(
        event_date=event_date,
        title=title,
        country_or_scope=country_or_scope,
        source=source,
        external_id=external_id,
        description=description,
    )
    db.add(row)
    return row


def sync_observances_for_years(db: Session, years: Iterable[int]) -> int:
    seed = _load_seed()
    count = 0
    for year in years:
        for item in seed:
            try:
                month = int(item["month"])
                day = int(item["day"])
                title = str(item["title"]).strip()
                external_id = f"{item.get('external_id') or title}-{year}"
                event_date = date(year, month, day)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping bad observance seed row %s: %s", item, exc)
                continue
            _upsert_event(
                db,
                event_date=event_date,
                title=title,
                country_or_scope="INT",
                source="observance",
                external_id=external_id,
                description=item.get("description"),
            )
            count += 1
    db.commit()
    return count


async def sync_nager_holidays(
    db: Session,
    countries: list[str],
    years: Iterable[int],
) -> int:
    count = 0
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        for year in years:
            for country in countries:
                code = (country or "").strip().upper()
                if not code:
                    continue
                url = NAGER_URL.format(year=year, country=code)
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    logger.warning("Nager.Date fetch failed for %s %s: %s", code, year, exc)
                    continue
                if not isinstance(payload, list):
                    continue
                for item in payload:
                    try:
                        event_date = date.fromisoformat(str(item["date"]))
                        title = str(item.get("localName") or item.get("name") or "").strip()
                        if not title:
                            continue
                        external_id = f"nager-{code}-{item.get('date')}-{title}"[:160]
                    except (KeyError, TypeError, ValueError):
                        continue
                    _upsert_event(
                        db,
                        event_date=event_date,
                        title=title,
                        country_or_scope=code,
                        source="nager",
                        external_id=external_id,
                        description=item.get("name"),
                    )
                    count += 1
    db.commit()
    return count


async def sync_calendar_events(
    db: Session,
    countries: Optional[list[str]] = None,
    *,
    years: Optional[list[int]] = None,
) -> dict:
    from app.services.un_observances_service import sync_un_observances

    today = date.today()
    year_list = years or [today.year, today.year + 1]
    country_list = countries or DEFAULT_COUNTRIES
    observance_count = sync_observances_for_years(db, year_list)
    un_count = await sync_un_observances(db, year_list)
    nager_count = await sync_nager_holidays(db, country_list, year_list)
    return {
        "observances_upserted": observance_count,
        "un_observances_upserted": un_count,
        "nager_upserted": nager_count,
        "countries": country_list,
        "years": year_list,
    }


def list_events(
    db: Session,
    *,
    date_from: date,
    date_to: date,
    countries: Optional[list[str]] = None,
    user_id: Optional[int] = None,
    limit: int = 500,
) -> list[CalendarEvent]:
    query = (
        db.query(CalendarEvent)
        .filter(CalendarEvent.event_date >= date_from, CalendarEvent.event_date <= date_to)
        .order_by(CalendarEvent.event_date.asc(), CalendarEvent.title.asc())
    )
    if countries:
        scopes = ["INT", "CUSTOM", *[c.strip().upper() for c in countries if c and c.strip()]]
        query = query.filter(CalendarEvent.country_or_scope.in_(scopes))
    rows = query.limit(limit).all()
    if user_id is None:
        return rows
    # Custom topics are user-scoped via external_id prefix user-{id}-…
    prefix = f"user-{user_id}-"
    return [
        row
        for row in rows
        if row.source != "custom" or (row.external_id or "").startswith(prefix)
    ]


def upcoming_week_window(today: Optional[date] = None) -> tuple[date, date]:
    """Inclusive 7-day window: today through today+6."""
    start = today or date.today()
    return start, start + timedelta(days=6)


def current_iso_week_window(today: Optional[date] = None) -> tuple[date, date]:
    """Monday–Sunday of the current ISO week (inclusive)."""
    start = today or date.today()
    monday = start - timedelta(days=start.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday
