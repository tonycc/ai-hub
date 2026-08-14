from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import Any, cast
from unittest.mock import AsyncMock

import jwt
import pytest
from ai_hub_platform.shared.token_validation import RegisteredOidcTokenValidator
from ai_hub_sdk import TokenValidationError, VerifiedToken
from ai_hub_sdk.identity import ActorType, OidcTokenValidator


def _routing_token(issuer: str, audience: str | list[str]) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","kid":"routing"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": issuer, "aud": audience}).encode()
    ).rstrip(b"=")
    signature = base64.urlsafe_b64encode(b"routing-signature").rstrip(b"=")
    return b".".join((header, payload, signature)).decode()


def _verified(*, issuer: str, audience: str, application_id: str | None) -> VerifiedToken:
    return VerifiedToken(
        subject="ak-rotating-app__production__v2-client_credentials",
        issuer=issuer,
        audience=(audience,),
        expires_at=2_000_000_000,
        issued_at=1_900_000_000,
        scopes=frozenset({"ai_hub.identity", "platform.application.read"}),
        actor_type="service",
        application_id=application_id,
        authorization_version=1,
        preferred_username=None,
        display_name=None,
        email=None,
        claims={},
    )


class StubValidator:
    def __init__(
        self,
        issuer: str,
        audience: str,
        result: VerifiedToken,
    ) -> None:
        self.issuer = issuer.rstrip("/") + "/"
        self.audience = audience
        self.result = result
        self.calls: list[tuple[str, tuple[str, ...], tuple[ActorType, ...]]] = []
        self.closed = False

    async def verify(
        self,
        token: str,
        *,
        required_scopes: Sequence[str] = (),
        allowed_actor_types: Sequence[ActorType] = ("user", "service"),
    ) -> VerifiedToken:
        self.calls.append((token, tuple(required_scopes), tuple(allowed_actor_types)))
        return self.result

    async def close(self) -> None:
        self.closed = True


def _registered(
    primary: StubValidator,
) -> RegisteredOidcTokenValidator:
    return RegisteredOidcTokenValidator(
        cast(OidcTokenValidator, primary),
        cast(Any, object()),
        cache_ttl_seconds=300,
        stale_ttl_seconds=3600,
    )


def test_unverified_routing_rejects_missing_or_invalid_claims() -> None:
    with pytest.raises(TokenValidationError, match="malformed"):
        RegisteredOidcTokenValidator._unverified_routing("not-a-jwt")  # pyright: ignore[reportPrivateUsage]

    token = jwt.encode(
        {"iss": "https://issuer.test/"},
        key="unused-but-long-enough-test-signing-key",
        algorithm="HS256",
    )
    with pytest.raises(TokenValidationError, match="lacks issuer or audience"):
        RegisteredOidcTokenValidator._unverified_routing(token)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_primary_issuer_does_not_query_registered_credentials() -> None:
    issuer = "https://identity.test/application/o/ai-hub/"
    primary = StubValidator(
        issuer,
        "ai-hub-platform",
        _verified(
            issuer=issuer,
            audience="ai-hub-platform",
            application_id="standalone-example",
        ),
    )
    validator = _registered(primary)
    validator._registered_route = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=AssertionError("primary tokens must not query application credentials")
    )

    result = await validator.verify(
        _routing_token(issuer, "ai-hub-platform"),
        required_scopes=("platform.application.read",),
        allowed_actor_types=("service",),
    )

    assert result.application_id == "standalone-example"
    assert len(primary.calls) == 1


@pytest.mark.asyncio
async def test_registered_issuer_uses_exact_audience_and_authoritative_application() -> None:
    primary = StubValidator(
        "https://identity.test/application/o/ai-hub/",
        "ai-hub-platform",
        _verified(
            issuer="https://identity.test/application/o/ai-hub/",
            audience="ai-hub-platform",
            application_id=None,
        ),
    )
    validator = _registered(primary)
    issuer = "https://identity.test/application/o/rotating-app-v2/"
    client_id = "rotating-app__production__v2"
    registered = StubValidator(
        issuer,
        client_id,
        _verified(issuer=issuer, audience=client_id, application_id=None),
    )
    route = AsyncMock(return_value=("rotating-app", client_id))
    factory = AsyncMock(return_value=registered)
    validator._registered_route = route  # pyright: ignore[reportPrivateUsage]
    validator._validator = factory  # pyright: ignore[reportPrivateUsage]

    result = await validator.verify(
        _routing_token(issuer, ["unrelated", client_id]),
        required_scopes=("platform.application.read",),
        allowed_actor_types=("service",),
    )

    assert result.application_id == "rotating-app"
    route.assert_awaited_once_with(issuer, ("unrelated", client_id))
    factory.assert_awaited_once_with(issuer, client_id)
    assert registered.calls[0][1:] == (
        ("platform.application.read",),
        ("service",),
    )


@pytest.mark.asyncio
async def test_registered_issuer_rejects_unknown_or_claim_mismatched_application() -> None:
    issuer = "https://identity.test/application/o/rotating-app-v2/"
    client_id = "rotating-app__production__v2"
    primary = StubValidator(
        "https://identity.test/application/o/ai-hub/",
        "ai-hub-platform",
        _verified(issuer=issuer, audience=client_id, application_id=None),
    )
    validator = _registered(primary)
    validator._registered_route = AsyncMock(return_value=None)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(TokenValidationError) as unknown:
        await validator.verify(_routing_token(issuer, client_id))
    assert unknown.value.error_code == "invalid_issuer"

    registered = StubValidator(
        issuer,
        client_id,
        _verified(
            issuer=issuer,
            audience=client_id,
            application_id="different-application",
        ),
    )
    validator._registered_route = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=("rotating-app", client_id)
    )
    validator._validator = AsyncMock(return_value=registered)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(TokenValidationError) as mismatch:
        await validator.verify(_routing_token(issuer, client_id))
    assert mismatch.value.error_code == "invalid_service_identity"


@pytest.mark.asyncio
async def test_registered_validator_cache_is_bounded_lru_and_all_clients_close() -> None:
    issuer = "https://identity.test/application/o/ai-hub/"
    primary = StubValidator(
        issuer,
        "ai-hub-platform",
        _verified(issuer=issuer, audience="ai-hub-platform", application_id=None),
    )
    validator = RegisteredOidcTokenValidator(
        cast(OidcTokenValidator, primary),
        cast(Any, object()),
        cache_ttl_seconds=300,
        stale_ttl_seconds=3600,
        maximum_registered_validators=1,
    )
    first = await validator._validator(  # pyright: ignore[reportPrivateUsage]
        "https://identity.test/application/o/one/", "one"
    )
    assert first is await validator._validator(  # pyright: ignore[reportPrivateUsage]
        "https://identity.test/application/o/one/", "one"
    )
    second = await validator._validator(  # pyright: ignore[reportPrivateUsage]
        "https://identity.test/application/o/two/", "two"
    )
    assert second is not first
    assert list(validator._registered) == [  # pyright: ignore[reportPrivateUsage]
        ("https://identity.test/application/o/two/", "two")
    ]

    await validator.close()
    assert primary.closed is True
