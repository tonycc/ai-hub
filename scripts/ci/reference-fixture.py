"""Explicit local/CI reference fixture. Never imported by the platform runtime.

Run via `docker compose exec -T platform-api python - < this-file` only in
an isolated runtime gate. Production environments are rejected.
"""

import asyncio
import json

from sqlalchemy import text

from ai_hub_platform.config import get_settings
from ai_hub_platform.modules.app_management.authentik import AuthentikAdminClient
from ai_hub_platform.modules.ingest.contract import schema_fingerprint
from ai_hub_platform.shared.database import Database


async def main() -> None:
    settings = get_settings()
    if settings.environment not in {"local", "test"}:
        raise RuntimeError("Reference fixtures are only permitted in local/test environments")
    database = Database(settings.database_url)
    authentik = AuthentikAdminClient(
        settings.authentik_api_url,
        settings.authentik_api_token.get_secret_value(),
        settings.authentik_external_url,
        settings.authentik_provider_template_client_id,
    )
    try:
        provider = None
        for _ in range(60):
            provider = await authentik._provider_by_client_id("standalone-example")
            if provider is not None:
                break
            await asyncio.sleep(2)
        if provider is None:
            raise RuntimeError("Install the reference blueprint before seeding fixtures")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {name: {"type": "string"} for name in ("name", "state", "owner_subject")},
            "required": ["name", "state", "owner_subject"],
        }
        async with database.session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("""
                    UPDATE platform_core.application SET status = 'ACTIVE',
                        service_subject = 'ak-standalone-example-client_credentials'
                    WHERE application_id = 'standalone-example'
                """)
                )
                await session.execute(
                    text("""
                    UPDATE platform_core.application_credential
                    SET client_id = 'standalone-example',
                        service_subject = 'ak-standalone-example-client_credentials',
                        issuer = :issuer, provider_external_id = :provider_id
                    WHERE credential_id = '31000000-0000-4000-8000-000000000001'
                      AND status = 'ACTIVE'
                """),
                    {
                        "issuer": settings.authentik_external_url.rstrip("/")
                        + "/application/o/standalone-example/",
                        "provider_id": str(provider["pk"]),
                    },
                )
                await session.execute(
                    text("""
                    INSERT INTO platform_core.ingest_contract
                        (source_application_id, object_type, contract_version, json_schema,
                         schema_fingerprint, field_classifications, compatibility_mode, origin, status)
                    VALUES ('standalone-example', 'example_record', 'example_record.v1',
                            CAST(:schema AS jsonb), :fingerprint, '{}', 'NONE', 'MANUAL', 'ACTIVE')
                    ON CONFLICT (source_application_id, object_type, contract_version) DO NOTHING
                """),
                    {"schema": json.dumps(schema), "fingerprint": schema_fingerprint(schema)},
                )
    finally:
        await authentik.close()
        await database.dispose()


asyncio.run(main())
