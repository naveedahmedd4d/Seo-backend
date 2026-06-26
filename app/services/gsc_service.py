import logging
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from app.services.http_ssl import httpx_verify

logger = logging.getLogger(__name__)

GSC_BASE = "https://www.googleapis.com/webmasters/v3"


def encode_site_url(site_url: str) -> str:
    return quote(site_url, safe="")


async def list_sites(access_token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
        response = await client.get(
            f"{GSC_BASE}/sites",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        logger.error("GSC list sites failed: %s", response.text)
        response.raise_for_status()
    payload = response.json()
    entries = payload.get("siteEntry") or []
    return [
        {
            "site_url": e.get("siteUrl"),
            "permission_level": e.get("permissionLevel"),
        }
        for e in entries
        if e.get("siteUrl")
    ]


async def query_search_analytics(
    access_token: str,
    site_url: str,
    start_date: date,
    end_date: date,
    dimensions: list[str],
    row_limit: int = 25000,
) -> list[dict[str, Any]]:
    encoded = encode_site_url(site_url)
    url = f"{GSC_BASE}/sites/{encoded}/searchAnalytics/query"
    all_rows: list[dict[str, Any]] = []
    start_row = 0

    async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
        while True:
            body = {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": dimensions,
                "rowLimit": row_limit,
                "startRow": start_row,
            }
            response = await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code >= 400:
                logger.error("GSC searchAnalytics failed: %s", response.text)
                response.raise_for_status()

            rows = response.json().get("rows") or []
            if not rows:
                break

            for row in rows:
                keys = row.get("keys") or []
                entry: dict[str, Any] = {
                    "clicks": int(row.get("clicks") or 0),
                    "impressions": int(row.get("impressions") or 0),
                    "ctr": float(row.get("ctr") or 0.0),
                    "position": float(row.get("position") or 0.0),
                }
                for idx, dim in enumerate(dimensions):
                    if idx < len(keys):
                        entry[dim] = keys[idx]
                all_rows.append(entry)

            if len(rows) < row_limit:
                break
            start_row += row_limit

    return all_rows
