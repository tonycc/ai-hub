from __future__ import annotations

import base64
import hashlib
import time
from typing import Any, cast
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from ai_hub_sdk import OidcClient, OidcTokenValidator, TokenValidationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://identity.test/application/o/ai-hub/"
AUDIENCE = "ai-hub-platform"
JWKS_URI = f"{ISSUER}jwks/"


def signing_material(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()

    def encode_integer(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    public_jwk: dict[str, Any] = {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": encode_integer(numbers.n),
        "e": encode_integer(numbers.e),
    }
    return private_key, public_jwk


def make_token(
    private_key: rsa.RSAPrivateKey,
    kid: str,
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_in: int = 300,
    scopes: str = "platform.me.read",
    actor_type: str = "user",
    application_id: str | None = None,
    include_identity_claims: bool = True,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "ai-hub-demo-user",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "scope": scopes,
    }
    if include_identity_claims:
        claims["actor_type"] = actor_type
        claims["authorization_version"] = 7
    if application_id is not None:
        claims["application_id"] = application_id
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def discovery() -> dict[str, str]:
    return {
        "issuer": ISSUER,
        "jwks_uri": JWKS_URI,
        "authorization_endpoint": f"{ISSUER}authorize/",
        "token_endpoint": f"{ISSUER}token/",
    }


@pytest.mark.asyncio
async def test_validator_uses_cached_discovery_and_jwks_for_normal_requests() -> None:
    key, jwk = signing_material("key-1")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if str(request.url).endswith("openid-configuration"):
            return httpx.Response(200, json=discovery())
        return httpx.Response(200, json={"keys": [jwk]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        validator = OidcTokenValidator(ISSUER, AUDIENCE, http_client=http)
        token = make_token(key, "key-1")
        first = await validator.verify(token, required_scopes=("platform.me.read",))
        second = await validator.verify(token, required_scopes=("platform.me.read",))

    assert first.subject == second.subject == "ai-hub-demo-user"
    assert requests == [f"{ISSUER}.well-known/openid-configuration", JWKS_URI]


@pytest.mark.asyncio
async def test_unknown_kid_triggers_exactly_one_jwks_refresh() -> None:
    first_key, first_jwk = signing_material("key-1")
    second_key, second_jwk = signing_material("key-2")
    jwks_fetches = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_fetches
        if str(request.url).endswith("openid-configuration"):
            return httpx.Response(200, json=discovery())
        jwks_fetches += 1
        keys = [first_jwk] if jwks_fetches == 1 else [first_jwk, second_jwk]
        return httpx.Response(200, json={"keys": keys})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        validator = OidcTokenValidator(ISSUER, AUDIENCE, http_client=http)
        await validator.verify(make_token(first_key, "key-1"))
        verified = await validator.verify(make_token(second_key, "key-2"))

    assert verified.subject == "ai-hub-demo-user"
    assert jwks_fetches == 2


@pytest.mark.asyncio
async def test_cached_key_survives_short_identity_provider_outage() -> None:
    key, jwk = signing_material("key-1")
    now = 0.0
    online = True

    def clock() -> float:
        return now

    def handler(request: httpx.Request) -> httpx.Response:
        if not online:
            raise httpx.ConnectError("identity provider offline", request=request)
        if str(request.url).endswith("openid-configuration"):
            return httpx.Response(200, json=discovery())
        return httpx.Response(200, json={"keys": [jwk]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        validator = OidcTokenValidator(
            ISSUER,
            AUDIENCE,
            cache_ttl_seconds=5,
            stale_ttl_seconds=60,
            http_client=http,
            clock=clock,
        )
        token = make_token(key, "key-1")
        await validator.verify(token)
        now = 10.0
        online = False
        verified = await validator.verify(token)

    assert verified.subject == "ai-hub-demo-user"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"issuer": "https://wrong.test/"}, "invalid_issuer"),
        ({"audience": "wrong-audience"}, "invalid_audience"),
        ({"expires_in": -10}, "token_expired"),
    ],
)
async def test_invalid_standard_claims_are_rejected(
    overrides: dict[str, object], error_code: str
) -> None:
    key, jwk = signing_material("key-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("openid-configuration"):
            return httpx.Response(200, json=discovery())
        return httpx.Response(200, json={"keys": [jwk]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        validator = OidcTokenValidator(ISSUER, AUDIENCE, http_client=http)
        token = make_token(key, "key-1", **cast(dict[str, Any], overrides))
        with pytest.raises(TokenValidationError) as error:
            await validator.verify(token)

    assert error.value.error_code == error_code


@pytest.mark.asyncio
async def test_missing_scope_and_invalid_service_identity_are_rejected() -> None:
    key, jwk = signing_material("key-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("openid-configuration"):
            return httpx.Response(200, json=discovery())
        return httpx.Response(200, json={"keys": [jwk]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        validator = OidcTokenValidator(ISSUER, AUDIENCE, http_client=http)
        with pytest.raises(TokenValidationError) as missing_scope:
            await validator.verify(
                make_token(key, "key-1", scopes="openid"),
                required_scopes=("platform.me.read",),
            )
        with pytest.raises(TokenValidationError) as invalid_service:
            await validator.verify(
                make_token(key, "key-1", actor_type="service"),
                allowed_actor_types=("service",),
            )

    assert missing_scope.value.error_code == "insufficient_scope"
    assert invalid_service.value.error_code == "invalid_service_identity"


@pytest.mark.asyncio
async def test_identity_claims_are_required_even_when_operation_scope_is_present() -> None:
    key, jwk = signing_material("key-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("openid-configuration"):
            return httpx.Response(200, json=discovery())
        return httpx.Response(200, json={"keys": [jwk]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        validator = OidcTokenValidator(ISSUER, AUDIENCE, http_client=http)
        with pytest.raises(TokenValidationError) as error:
            await validator.verify(
                make_token(key, "key-1", include_identity_claims=False),
                required_scopes=("platform.me.read",),
            )

    assert error.value.error_code == "invalid_token"


@pytest.mark.asyncio
async def test_client_credentials_token_is_cached_and_pkce_is_s256() -> None:
    token_requests = 0
    now = 0.0

    def clock() -> float:
        return now

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.method == "GET":
            return httpx.Response(200, json=discovery())
        token_requests += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"service-token-{token_requests}",
                "token_type": "Bearer",
                "expires_in": 120,
                "scope": "platform.notification.request",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OidcClient(
            ISSUER,
            "client",
            "secret",
            http_client=http,
            clock=clock,
        )
        authorization = await client.create_authorization_request(
            "https://app.test/callback",
            scopes=("openid", "platform.me.read"),
            nonce="nonce-value",
        )
        first = await client.client_credentials_token(("platform.notification.request",))
        second = await client.client_credentials_token(("platform.notification.request",))

    expected = base64.urlsafe_b64encode(
        hashlib.sha256(authorization.code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert f"code_challenge={expected}" in authorization.url
    assert "code_challenge_method=S256" in authorization.url
    assert "nonce=nonce-value" in authorization.url
    assert authorization.nonce == "nonce-value"
    assert first == second == "service-token-1"
    assert token_requests == 1


@pytest.mark.asyncio
async def test_client_credentials_cache_is_isolated_by_normalized_scope_set() -> None:
    requested_scopes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=discovery())
        form = parse_qs(request.content.decode())
        scope = form.get("scope", [""])[0]
        requested_scopes.append(scope)
        return httpx.Response(
            200,
            json={
                "access_token": f"token-{len(requested_scopes)}",
                "token_type": "Bearer",
                "expires_in": 120,
                "scope": scope,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OidcClient(ISSUER, "client", "secret", http_client=http)
        narrow = await client.client_credentials_token(("scope.b", "scope.a"))
        same_set = await client.client_credentials_token(("scope.a", "scope.b", "scope.a"))
        broad = await client.client_credentials_token(("scope.a", "scope.b", "scope.c"))

    assert narrow == same_set == "token-1"
    assert broad == "token-2"
    assert len(requested_scopes) == 2
