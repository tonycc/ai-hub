"""AI/governance consumer example for aggregated data reads (M7-03)."""

from __future__ import annotations

import asyncio
import os

import httpx
from ai_hub_sdk import OidcClient


async def main() -> None:
    platform_url = os.environ["AI_HUB_PLATFORM_URL"].rstrip("/")
    issuer = os.environ["AI_HUB_OIDC_ISSUER"]
    client_id = os.environ["AI_HUB_CLIENT_ID"]
    client_secret = os.environ["AI_HUB_CLIENT_SECRET"]
    source_application_id = os.environ.get(
        "AI_HUB_DATA_SOURCE_APPLICATION_ID", "standalone-example"
    )
    object_type = os.environ.get("AI_HUB_DATA_OBJECT_TYPE", "device")

    oidc = OidcClient(issuer, client_id, client_secret)
    try:
        token = await oidc.client_credentials_token(
            ("ai_hub.identity", "platform.data.read")
        )
        async with httpx.AsyncClient(base_url=platform_url, timeout=30.0) as client:
            response = await client.get(
                "/platform-api/v1/data/objects",
                params={
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                    "limit": 20,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            payload = response.json()
            print(
                "objects",
                payload["total"],
                [item["object_id"] for item in payload["items"]],
            )
            if payload["items"]:
                first = payload["items"][0]
                history = await client.get(
                    "/platform-api/v1/data/objects/"
                    f"{first['source_application_id']}/{first['object_type']}/"
                    f"{first['object_id']}/history",
                    headers={"Authorization": f"Bearer {token}"},
                )
                history.raise_for_status()
                print("history", history.json()["total"])
    finally:
        await oidc.close()


if __name__ == "__main__":
    asyncio.run(main())
