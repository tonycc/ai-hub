"""Minimal service-identity connectivity check without business authorization."""
import asyncio
import os

from ai_hub_sdk import AiHubClient, OidcClient


async def main() -> None:
    oidc = OidcClient(
        os.environ['AI_HUB_OIDC_ISSUER'], os.environ['AI_HUB_CLIENT_ID'],
        os.environ['AI_HUB_CLIENT_SECRET'],
    )
    platform = AiHubClient(
        os.environ['AI_HUB_PLATFORM_URL'],
        token_provider=lambda: oidc.client_credentials_token(
            ('ai_hub.identity', 'platform.application.read')
        ),
    )
    try:
        health = await platform.health()
        application = await platform.application(os.environ['AI_HUB_APPLICATION_ID'])
        print(health.status, application.application_id, application.status)
    finally:
        await platform.close()
        await oidc.close()


if __name__ == '__main__':
    asyncio.run(main())
