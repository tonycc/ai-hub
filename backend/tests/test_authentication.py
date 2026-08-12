from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, NoReturn

import httpx
import pytest
from ai_hub_platform.api.dependencies import Principal, principal_dependency
from ai_hub_platform.api.errors import register_error_handlers
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.shared.observability import RequestContextMiddleware
from ai_hub_sdk import TokenValidationError
from ai_hub_sdk.identity import ActorType
from fastapi import Depends, FastAPI


class RejectingValidator:
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code

    async def verify(
        self,
        token: str,
        *,
        required_scopes: Sequence[str] = (),
        allowed_actor_types: Sequence[ActorType] = ("user", "service"),
    ) -> NoReturn:
        _ = token, required_scopes, allowed_actor_types
        raise TokenValidationError(self.error_code, "rejected for test")


class CapturingValidator:
    def __init__(self) -> None:
        self.required_scopes: Sequence[str] = ()

    async def verify(
        self,
        token: str,
        *,
        required_scopes: Sequence[str] = (),
        allowed_actor_types: Sequence[ActorType] = ("user", "service"),
    ):
        from ai_hub_sdk import VerifiedToken

        _ = token, allowed_actor_types
        self.required_scopes = required_scopes
        return VerifiedToken(
            subject="ai-hub-demo-user",
            issuer="https://identity.test/",
            audience=("ai-hub-platform",),
            expires_at=2_000_000_000,
            issued_at=1_900_000_000,
            scopes=frozenset(required_scopes),
            actor_type="user",
            application_id=None,
            authorization_version=1,
            preferred_username="ai-hub-demo-user",
            display_name="AI Hub Demo User",
            email=None,
            claims={},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "expected_status"),
    [
        ("invalid_issuer", 401),
        ("invalid_audience", 401),
        ("token_expired", 401),
        ("insufficient_scope", 403),
    ],
)
async def test_token_rejections_are_audited_independently(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    expected_status: int,
) -> None:
    audits: list[AuditRecord] = []

    async def append_committed(
        self: AuditService, database: object, record: AuditRecord
    ) -> None:
        _ = self, database
        audits.append(record)

    monkeypatch.setattr(AuditService, "append_committed", append_committed)
    application = FastAPI()
    application.state.database = object()
    application.state.token_validator = RejectingValidator(error_code)
    application.add_middleware(RequestContextMiddleware)
    register_error_handlers(application)

    @application.get("/protected")
    async def protected(
        principal: Annotated[
            Principal,
            Depends(principal_dependency("platform.me.read", actor_types=("user",))),
        ],
    ) -> dict[str, str]:
        return {"subject": principal.token.subject}

    _ = protected
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://platform.test") as client:
        response = await client.get(
            "/protected",
            headers={
                "Authorization": "Bearer rejected-token",
                "X-Application-ID": "standalone-example",
                "X-Request-ID": f"audit-{error_code}",
            },
        )

    assert response.status_code == expected_status
    assert response.json()["error_code"] == error_code
    assert audits == [
        AuditRecord(
            request_id=f"audit-{error_code}",
            action="platform.access.authenticate",
            result="DENIED",
            application_id="standalone-example",
            target_type="api_path",
            target_id="/protected",
            error_code=error_code,
            audit_id=audits[0].audit_id,
        )
    ]


@pytest.mark.asyncio
async def test_every_platform_principal_requires_identity_scope() -> None:
    validator = CapturingValidator()
    application = FastAPI()
    application.state.database = object()
    application.state.token_validator = validator
    application.add_middleware(RequestContextMiddleware)
    register_error_handlers(application)

    @application.get("/protected")
    async def protected(
        principal: Annotated[
            Principal,
            Depends(principal_dependency("platform.me.read", actor_types=("user",))),
        ],
    ) -> dict[str, str]:
        return {"subject": principal.token.subject}

    _ = protected
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://platform.test") as client:
        response = await client.get(
            "/protected", headers={"Authorization": "Bearer accepted-token"}
        )

    assert response.status_code == 200
    assert tuple(validator.required_scopes) == ("ai_hub.identity", "platform.me.read")
