import asyncio
import httpx
from app.services.http_ssl import httpx_verify


async def test():
    async with httpx.AsyncClient(timeout=15.0, verify=httpx_verify()) as client:
        r = await client.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_news_light",
                "q": "test",
                "num": 1,
                "api_key": "dummy",
            },
        )
        print("Status:", r.status_code)
        print("SSL OK")


asyncio.run(test())
