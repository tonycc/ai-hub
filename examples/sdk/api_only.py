"""Minimal API-only integration; no Outbox, Inbox, or RabbitMQ dependency."""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

from ai_hub_sdk import AiHubClient, NotificationRequest, OidcClient


async def main() -> None:
    platform_url = os.environ["AI_HUB_PLATFORM_URL"]
    issuer = os.environ["AI_HUB_OIDC_ISSUER"]
    client_id = os.environ["AI_HUB_CLIENT_ID"]
    client_secret = os.environ["AI_HUB_CLIENT_SECRET"]
    application_id = os.environ["AI_HUB_APPLICATION_ID"]
    recipient_user_id = UUID(os.environ["AI_HUB_RECIPIENT_USER_ID"])

    oidc = OidcClient(issuer, client_id, client_secret)
    platform = AiHubClient(
        platform_url,
        token_provider=lambda: oidc.client_credentials_token(
            ("ai_hub.identity", "platform.notification.request")
        ),
    )
    try:
        health = await platform.health()
        notification = await platform.create_notification(
            NotificationRequest(
                recipient_user_id=recipient_user_id,
                subject="AI Hub API-only connectivity test",
                body="This request uses only OIDC and the public platform API.",
                idempotency_key="api-only-quickstart-v1",
                payload={"example": True},
            )
        )
        if notification.application_id != application_id:
            raise RuntimeError("Platform returned a notification for another application")
        print(health.status, notification.status, notification.notification_id)
    finally:
        await platform.close()
        await oidc.close()


if __name__ == "__main__":
    asyncio.run(main())
