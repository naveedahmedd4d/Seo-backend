"""Backend-only Apify adapter for Reddit Trends.

The rest of the Reddit feature depends on this small interface instead of the
Actor's concrete payload shape, so another provider can replace it later.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.services.http_ssl import httpx_verify

APIFY_BASE = "https://api.apify.com/v2"
TERMINAL_FAILURES = {"FAILED", "ABORTED", "TIMED-OUT"}


class ApifyRedditError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ApifyRunHandle:
    run_id: str
    dataset_id: str


async def _request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    attempts: int = 3,
) -> httpx.Response:
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            response = await client.request(method, url, headers=headers, json=json, params=params)
            if response.status_code not in (429, 500, 502, 503, 504):
                return response
            last_error = ApifyRedditError(
                f"Apify temporarily unavailable ({response.status_code})",
                response.status_code,
            )
            retry_after = response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
            await asyncio.sleep(min(wait, 10.0))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                await asyncio.sleep(2 ** attempt)
    raise ApifyRedditError(f"Apify request failed after retries: {last_error}")


class ApifyRedditProvider:
    def __init__(self, api_token: str, actor_id: Optional[str] = None):
        self.api_token = api_token.strip()
        self.actor_id = (actor_id or settings.apify_reddit_actor_id).strip()
        if not self.api_token:
            raise ValueError("Add your Apify API key in Settings")
        if not self.actor_id:
            raise ValueError("APIFY_REDDIT_ACTOR_ID is not configured")

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    async def start_run(self, run_input: Dict[str, Any]) -> ApifyRunHandle:
        url = f"{APIFY_BASE}/acts/{self.actor_id}/runs"
        async with httpx.AsyncClient(timeout=45.0, verify=httpx_verify()) as client:
            response = await _request_with_retries(
                client, "POST", url, headers=self.headers, json=run_input
            )
        if response.status_code == 401:
            raise ApifyRedditError("Invalid Apify API key", 401)
        if response.status_code == 402:
            raise ApifyRedditError("Apify account has insufficient credits", 402)
        if response.status_code >= 400:
            raise ApifyRedditError(
                f"Apify could not start the Reddit Actor ({response.status_code})",
                response.status_code,
            )
        data = (response.json() or {}).get("data") or {}
        run_id = data.get("id")
        dataset_id = data.get("defaultDatasetId")
        if not run_id or not dataset_id:
            raise ApifyRedditError("Apify run response is missing its run or dataset ID")
        return ApifyRunHandle(run_id=str(run_id), dataset_id=str(dataset_id))

    async def wait_for_run(self, run_id: str) -> str:
        timeout = max(60, int(settings.reddit_trends_run_timeout_seconds))
        poll = max(1.0, float(settings.reddit_trends_poll_interval_seconds))
        started = time.monotonic()
        url = f"{APIFY_BASE}/actor-runs/{run_id}"
        async with httpx.AsyncClient(timeout=30.0, verify=httpx_verify()) as client:
            while time.monotonic() - started < timeout:
                response = await _request_with_retries(
                    client, "GET", url, headers=self.headers
                )
                if response.status_code >= 400:
                    raise ApifyRedditError(
                        f"Could not read Apify run status ({response.status_code})",
                        response.status_code,
                    )
                state = str(((response.json() or {}).get("data") or {}).get("status") or "").upper()
                if state == "SUCCEEDED":
                    return state
                if state in TERMINAL_FAILURES:
                    raise ApifyRedditError(f"Apify Reddit run ended with status {state}")
                await asyncio.sleep(poll)
        await self.abort_run(run_id)
        raise ApifyRedditError("Apify Reddit run timed out")

    async def fetch_dataset(self, dataset_id: str, hard_limit: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        offset = 0
        page_size = min(250, max(1, hard_limit))
        url = f"{APIFY_BASE}/datasets/{dataset_id}/items"
        async with httpx.AsyncClient(timeout=60.0, verify=httpx_verify()) as client:
            while len(items) < hard_limit:
                response = await _request_with_retries(
                    client,
                    "GET",
                    url,
                    headers=self.headers,
                    params={"offset": offset, "limit": page_size, "clean": "true"},
                )
                if response.status_code >= 400:
                    raise ApifyRedditError(
                        f"Could not retrieve Apify dataset ({response.status_code})",
                        response.status_code,
                    )
                payload = response.json()
                page = payload if isinstance(payload, list) else []
                clean_page = [item for item in page if isinstance(item, dict)]
                items.extend(clean_page[: hard_limit - len(items)])
                if len(page) < page_size:
                    break
                offset += len(page)
        return items

    async def abort_run(self, run_id: str) -> None:
        url = f"{APIFY_BASE}/actor-runs/{run_id}/abort"
        try:
            async with httpx.AsyncClient(timeout=20.0, verify=httpx_verify()) as client:
                await client.post(url, headers=self.headers)
        except Exception:
            # Timeout cleanup is best effort; the original failure remains primary.
            return
