from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

JsonObject = dict[str, Any]
ActorType = Literal["user", "service"]


class TokenValidationError(ValueError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class OAuthProtocolError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class _TokenClaims(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub: str = Field(min_length=1)
    iss: str
    aud: str | list[str]
    exp: int
    iat: int
    scope: str | list[str] = ""
    actor_type: ActorType
    application_id: str | None = None
    authorization_version: int = Field(ge=1)
    preferred_username: str | None = None
    name: str | None = None
    email: str | None = None

    @field_validator("scope", mode="before")
    @classmethod
    def normalize_scope(cls, value: object) -> object:
        if value is None:
            return ""
        return value


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    subject: str
    issuer: str
    audience: tuple[str, ...]
    expires_at: int
    issued_at: int
    scopes: frozenset[str]
    actor_type: ActorType
    application_id: str | None
    authorization_version: int
    preferred_username: str | None
    display_name: str | None
    email: str | None
    claims: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    url: str
    state: str
    code_verifier: str
    nonce: str | None = None


class OAuthToken(BaseModel):
    access_token: str
    token_type: str
    expires_in: int = Field(gt=0)
    scope: str = ""
    refresh_token: str | None = None
    id_token: str | None = None


@dataclass(slots=True)
class _CacheEntry:
    value: JsonObject
    expires_at: float
    stale_until: float


class OidcTokenValidator:
    """Asynchronous local JWT validator with bounded Discovery/JWKS caches."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        cache_ttl_seconds: int = 300,
        stale_ttl_seconds: int = 3600,
        algorithms: Sequence[str] = ("RS256",),
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.issuer = issuer.rstrip("/") + "/"
        self.audience = audience
        self.cache_ttl_seconds = cache_ttl_seconds
        self.stale_ttl_seconds = stale_ttl_seconds
        self.algorithms = tuple(algorithms)
        self._clock = clock
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0)
        )
        self._discovery: _CacheEntry | None = None
        self._jwks: _CacheEntry | None = None
        self._discovery_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

    async def _fetch_json(self, url: str) -> JsonObject:
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise TokenValidationError(
                "identity_provider_unavailable",
                "OIDC metadata or signing keys are unavailable",
            ) from error
        if not isinstance(payload, dict):
            raise TokenValidationError("invalid_oidc_metadata", "OIDC response must be an object")
        return cast(JsonObject, payload)

    def _fresh(self, entry: _CacheEntry | None) -> bool:
        return entry is not None and self._clock() < entry.expires_at

    def _stale_usable(self, entry: _CacheEntry | None) -> bool:
        return entry is not None and self._clock() < entry.stale_until

    def _new_entry(self, value: JsonObject) -> _CacheEntry:
        now = self._clock()
        return _CacheEntry(
            value=value,
            expires_at=now + self.cache_ttl_seconds,
            stale_until=now + self.stale_ttl_seconds,
        )

    async def _get_discovery(self) -> JsonObject:
        if self._fresh(self._discovery):
            return cast(_CacheEntry, self._discovery).value
        async with self._discovery_lock:
            if self._fresh(self._discovery):
                return cast(_CacheEntry, self._discovery).value
            try:
                payload = await self._fetch_json(
                    f"{self.issuer}.well-known/openid-configuration"
                )
            except TokenValidationError:
                if self._stale_usable(self._discovery):
                    return cast(_CacheEntry, self._discovery).value
                raise
            if payload.get("issuer") != self.issuer:
                raise TokenValidationError(
                    "invalid_oidc_metadata", "Discovery issuer does not match configuration"
                )
            jwks_uri = payload.get("jwks_uri")
            if not isinstance(jwks_uri, str) or not jwks_uri:
                raise TokenValidationError("invalid_oidc_metadata", "Discovery lacks jwks_uri")
            self._discovery = self._new_entry(payload)
            return payload

    async def _discovery_for_jwks(self) -> JsonObject:
        if self._fresh(self._discovery):
            return cast(_CacheEntry, self._discovery).value
        try:
            payload = await self._fetch_json(
                f"{self.issuer}.well-known/openid-configuration"
            )
        except TokenValidationError:
            if self._stale_usable(self._discovery):
                return cast(_CacheEntry, self._discovery).value
            raise
        if payload.get("issuer") != self.issuer:
            raise TokenValidationError(
                "invalid_oidc_metadata", "Discovery issuer does not match configuration"
            )
        jwks_uri = payload.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise TokenValidationError("invalid_oidc_metadata", "Discovery lacks jwks_uri")
        self._discovery = self._new_entry(payload)
        return payload

    async def _get_jwks(self, *, force_refresh: bool = False) -> JsonObject:
        if not force_refresh and self._fresh(self._jwks):
            return cast(_CacheEntry, self._jwks).value
        async with self._jwks_lock:
            if not force_refresh and self._fresh(self._jwks):
                return cast(_CacheEntry, self._jwks).value
            discovery = await self._discovery_for_jwks()
            jwks_uri = cast(str, discovery["jwks_uri"])
            try:
                payload = await self._fetch_json(jwks_uri)
            except TokenValidationError:
                if not force_refresh and self._stale_usable(self._jwks):
                    return cast(_CacheEntry, self._jwks).value
                raise
            keys = payload.get("keys")
            if not isinstance(keys, list):
                raise TokenValidationError("invalid_jwks", "JWKS response lacks a keys array")
            self._jwks = self._new_entry(payload)
            return payload

    @staticmethod
    def _find_key(jwks: JsonObject, kid: str) -> JsonObject | None:
        raw_keys: object = jwks.get("keys")
        if not isinstance(raw_keys, list):
            return None
        for untyped_key in cast(list[object], raw_keys):
            if not isinstance(untyped_key, dict):
                continue
            raw_key = cast(JsonObject, untyped_key)
            if raw_key.get("kid") == kid:
                return raw_key
        return None

    async def verify(
        self,
        token: str,
        *,
        required_scopes: Sequence[str] = (),
        allowed_actor_types: Sequence[ActorType] = ("user", "service"),
    ) -> VerifiedToken:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise TokenValidationError("invalid_token", "Bearer token is malformed") from error
        kid = header.get("kid")
        algorithm = header.get("alg")
        if not isinstance(kid, str) or not kid:
            raise TokenValidationError("invalid_token", "Bearer token lacks a signing key id")
        if not isinstance(algorithm, str) or algorithm not in self.algorithms:
            raise TokenValidationError(
                "invalid_token", "Bearer token uses an unsupported algorithm"
            )

        jwks = await self._get_jwks()
        raw_key = self._find_key(jwks, kid)
        if raw_key is None:
            jwks = await self._get_jwks(force_refresh=True)
            raw_key = self._find_key(jwks, kid)
        if raw_key is None:
            raise TokenValidationError("unknown_signing_key", "Bearer token key id is unknown")

        try:
            signing_key = jwt.PyJWK.from_dict(raw_key, algorithm=algorithm).key
            decoded = jwt.decode(
                token,
                key=signing_key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            claims = _TokenClaims.model_validate(decoded)
        except jwt.ExpiredSignatureError as error:
            raise TokenValidationError("token_expired", "Bearer token has expired") from error
        except jwt.InvalidIssuerError as error:
            raise TokenValidationError(
                "invalid_issuer", "Bearer token issuer is invalid"
            ) from error
        except jwt.InvalidAudienceError as error:
            raise TokenValidationError(
                "invalid_audience", "Bearer token audience is invalid"
            ) from error
        except (jwt.PyJWTError, ValidationError, ValueError) as error:
            raise TokenValidationError("invalid_token", "Bearer token validation failed") from error

        scope_values = (
            claims.scope.split() if isinstance(claims.scope, str) else list(claims.scope)
        )
        scopes = frozenset(scope for scope in scope_values if scope)
        missing_scopes = sorted(set(required_scopes) - scopes)
        if missing_scopes:
            raise TokenValidationError(
                "insufficient_scope", f"Required scope is missing: {missing_scopes[0]}"
            )
        if claims.actor_type not in allowed_actor_types:
            raise TokenValidationError(
                "invalid_actor_type", "Bearer token actor type is not allowed for this operation"
            )
        if claims.actor_type == "service" and not claims.application_id:
            raise TokenValidationError(
                "invalid_service_identity", "Service token lacks application_id"
            )

        audience = (claims.aud,) if isinstance(claims.aud, str) else tuple(claims.aud)
        return VerifiedToken(
            subject=claims.sub,
            issuer=claims.iss,
            audience=audience,
            expires_at=claims.exp,
            issued_at=claims.iat,
            scopes=scopes,
            actor_type=claims.actor_type,
            application_id=claims.application_id,
            authorization_version=claims.authorization_version,
            preferred_username=claims.preferred_username,
            display_name=claims.name,
            email=claims.email,
            claims=cast(Mapping[str, Any], claims.model_dump()),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()


class OidcClient:
    """OIDC authorization-code/PKCE and client-credentials helper."""

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        token_leeway_seconds: int = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.issuer = issuer.rstrip("/") + "/"
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_leeway_seconds = token_leeway_seconds
        self._clock = clock
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0)
        )
        self._discovery: JsonObject | None = None
        self._service_tokens: dict[tuple[str, ...], tuple[OAuthToken, float]] = {}
        self._lock = asyncio.Lock()

    async def _metadata(self) -> JsonObject:
        if self._discovery is not None:
            return self._discovery
        try:
            response = await self._http.get(
                f"{self.issuer}.well-known/openid-configuration"
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OAuthProtocolError(
                "identity_provider_unavailable", "OIDC discovery is unavailable"
            ) from error
        if not isinstance(payload, dict):
            raise OAuthProtocolError("invalid_oidc_metadata", "OIDC discovery is invalid")
        metadata = cast(JsonObject, payload)
        if metadata.get("issuer") != self.issuer:
            raise OAuthProtocolError("invalid_oidc_metadata", "OIDC discovery is invalid")
        self._discovery = metadata
        return self._discovery

    async def create_authorization_request(
        self,
        redirect_uri: str,
        *,
        scopes: Sequence[str],
        nonce: str | None = None,
    ) -> AuthorizationRequest:
        metadata = await self._metadata()
        endpoint = metadata.get("authorization_endpoint")
        if not isinstance(endpoint, str):
            raise OAuthProtocolError(
                "invalid_oidc_metadata", "Discovery lacks authorization_endpoint"
            )
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        query_parameters = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if nonce is not None:
            query_parameters["nonce"] = nonce
        query = urlencode(query_parameters)
        return AuthorizationRequest(
            url=f"{endpoint}?{query}",
            state=state,
            code_verifier=verifier,
            nonce=nonce,
        )

    async def _token_request(self, data: Mapping[str, str]) -> OAuthToken:
        metadata = await self._metadata()
        endpoint = metadata.get("token_endpoint")
        if not isinstance(endpoint, str):
            raise OAuthProtocolError("invalid_oidc_metadata", "Discovery lacks token_endpoint")
        try:
            response = await self._http.post(
                endpoint,
                data=data,
                auth=(self.client_id, self.client_secret),
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OAuthProtocolError(
                "identity_provider_unavailable", "OIDC token endpoint is unavailable"
            ) from error
        if response.is_error:
            error_code = "token_request_rejected"
            message = "OIDC token request was rejected"
            if isinstance(payload, dict):
                error_payload = cast(JsonObject, payload)
                raw_code = error_payload.get("error")
                raw_message = error_payload.get("error_description")
                if isinstance(raw_code, str):
                    error_code = raw_code
                if isinstance(raw_message, str):
                    message = raw_message
            raise OAuthProtocolError(error_code, message)
        try:
            return OAuthToken.model_validate(payload)
        except ValidationError as error:
            raise OAuthProtocolError(
                "invalid_token_response", "OIDC token response is invalid"
            ) from error

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthToken:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    async def client_credentials_token(self, scopes: Sequence[str]) -> str:
        scope_key = tuple(sorted(set(scopes)))
        cached = self._service_tokens.get(scope_key)
        if cached is not None and self._clock() + self.token_leeway_seconds < cached[1]:
            return cached[0].access_token
        async with self._lock:
            cached = self._service_tokens.get(scope_key)
            if cached is not None and self._clock() + self.token_leeway_seconds < cached[1]:
                return cached[0].access_token
            token = await self._token_request(
                {"grant_type": "client_credentials", "scope": " ".join(scope_key)}
            )
            self._service_tokens[scope_key] = (
                token,
                self._clock() + token.expires_in,
            )
            return token.access_token

    def clear_service_token(self) -> None:
        self._service_tokens.clear()

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()
