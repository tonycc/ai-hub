from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import replace
from typing import cast

import httpx
import jwt
import sqlalchemy as sa
from ai_hub_sdk import OidcTokenValidator, TokenValidationError, VerifiedToken
from ai_hub_sdk.identity import ActorType

from ai_hub_platform.shared.database import Database


class RegisteredOidcTokenValidator:
    """Validate the shared issuer and registered per-credential OIDC issuers.

    Unverified claims are used only to select an exact database-registered
    issuer/audience pair. The selected validator then performs normal Discovery,
    JWKS, signature, issuer, audience, lifetime, actor, and scope validation.
    """

    def __init__(
        self,
        primary: OidcTokenValidator,
        database: Database,
        *,
        cache_ttl_seconds: int,
        stale_ttl_seconds: int,
        maximum_registered_validators: int = 128,
    ) -> None:
        if maximum_registered_validators < 1:
            raise ValueError("maximum_registered_validators must be positive")
        self._primary = primary
        self._database = database
        self._cache_ttl_seconds = cache_ttl_seconds
        self._stale_ttl_seconds = stale_ttl_seconds
        self._maximum_registered_validators = maximum_registered_validators
        self._registered_http: httpx.AsyncClient | None = None
        self._registered: OrderedDict[tuple[str, str], OidcTokenValidator] = OrderedDict()
        self._lock = asyncio.Lock()

    @staticmethod
    def _unverified_routing(token: str) -> tuple[str, tuple[str, ...]]:
        try:
            payload = jwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_aud": False,
                    "verify_iss": False,
                },
                algorithms=["RS256"],
            )
        except jwt.PyJWTError as error:
            raise TokenValidationError("invalid_token", "Bearer token is malformed") from error
        issuer = payload.get("iss")
        audience_value = payload.get("aud")
        audiences = (
            (audience_value,)
            if isinstance(audience_value, str)
            else tuple(
                item
                for item in cast(list[object], audience_value)
                if isinstance(item, str)
            )
            if isinstance(audience_value, list)
            else ()
        )
        if not isinstance(issuer, str) or not issuer or not audiences:
            raise TokenValidationError(
                "invalid_token",
                "Bearer token lacks issuer or audience routing claims",
            )
        return issuer.rstrip("/") + "/", audiences

    async def _registered_route(
        self,
        issuer: str,
        audiences: tuple[str, ...],
    ) -> tuple[str, str] | None:
        async with self._database.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        sa.text(
                            """
                            SELECT c.application_id, c.client_id
                            FROM platform_core.application_credential AS c
                            JOIN platform_core.application AS a
                              ON a.application_id = c.application_id
                            JOIN platform_core.application_environment AS e
                              ON e.application_id = c.application_id
                             AND e.environment = c.environment
                            WHERE c.issuer = :issuer
                              AND c.client_id = ANY(CAST(:audiences AS varchar[]))
                              AND c.status IN ('ACTIVE', 'DRAINING', 'REVOKED')
                              AND a.status = 'ACTIVE'
                              AND e.status = 'ACTIVE'
                              AND (c.expires_at IS NULL
                                   OR c.expires_at > CURRENT_TIMESTAMP)
                            ORDER BY c.version DESC
                            LIMIT 2
                            """
                        ),
                        {"issuer": issuer, "audiences": list(audiences)},
                    )
                )
                .mappings()
                .all()
            )
        if not rows:
            return None
        if len(rows) != 1:
            raise TokenValidationError(
                "invalid_token",
                "Bearer token issuer and audience are ambiguous",
            )
        return str(rows[0]["application_id"]), str(rows[0]["client_id"])

    async def _validator(self, issuer: str, audience: str) -> OidcTokenValidator:
        key = (issuer, audience)
        existing = self._registered.get(key)
        if existing is not None:
            self._registered.move_to_end(key)
            return existing
        async with self._lock:
            existing = self._registered.get(key)
            if existing is not None:
                self._registered.move_to_end(key)
                return existing
            if len(self._registered) >= self._maximum_registered_validators:
                # Every dynamic validator shares one HTTP connection pool, so an
                # evicted object owns no socket resources and remains safe for an
                # already-running verification that still holds a reference.
                self._registered.popitem(last=False)
            if self._registered_http is None:
                self._registered_http = httpx.AsyncClient(
                    timeout=httpx.Timeout(5.0, connect=2.0),
                )
            validator = OidcTokenValidator(
                issuer,
                audience,
                cache_ttl_seconds=self._cache_ttl_seconds,
                stale_ttl_seconds=self._stale_ttl_seconds,
                http_client=self._registered_http,
            )
            self._registered[key] = validator
            return validator

    async def verify(
        self,
        token: str,
        *,
        required_scopes: tuple[str, ...] = (),
        allowed_actor_types: tuple[ActorType, ...] = ("user", "service"),
    ) -> VerifiedToken:
        issuer, audiences = self._unverified_routing(token)
        if issuer == self._primary.issuer:
            return await self._primary.verify(
                token,
                required_scopes=required_scopes,
                allowed_actor_types=allowed_actor_types,
            )
        route = await self._registered_route(issuer, audiences)
        if route is None:
            raise TokenValidationError(
                "invalid_issuer",
                "Bearer token issuer is not registered",
            )
        application_id, audience = route
        validator = await self._validator(issuer, audience)
        verified = await validator.verify(
            token,
            required_scopes=required_scopes,
            allowed_actor_types=allowed_actor_types,
        )
        if verified.application_id not in {None, application_id}:
            raise TokenValidationError(
                "invalid_service_identity",
                "Service token application does not match its registered credential",
            )
        return replace(verified, application_id=application_id)

    async def close(self) -> None:
        validators = [self._primary, *self._registered.values()]
        self._registered.clear()
        for validator in validators:
            await validator.close()
        if self._registered_http is not None:
            await self._registered_http.aclose()
            self._registered_http = None
