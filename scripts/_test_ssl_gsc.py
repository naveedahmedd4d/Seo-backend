import asyncio
import ssl

import certifi
import httpx

from app.services.http_ssl import httpx_verify


async def probe(label: str, verify) -> None:
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=verify) as client:
            response = await client.get(
                "https://www.googleapis.com/webmasters/v3/sites",
                headers={"Authorization": "Bearer invalid"},
            )
        print(f"{label}: OK status={response.status_code}")
    except Exception as exc:
        print(f"{label}: FAIL {type(exc).__name__}: {exc}")


async def main() -> None:
    await probe("current httpx_verify", httpx_verify())
    await probe("certifi path", certifi.where())
    await probe(
        "certifi ctx",
        ssl.create_default_context(cafile=certifi.where()),
    )
    try:
        import truststore

        await probe(
            "truststore",
            truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
    except ImportError:
        print("truststore: not installed")


if __name__ == "__main__":
    asyncio.run(main())
