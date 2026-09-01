from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Any, cast

import httpx

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProvisionedCredential:
    client_id: str
    client_secret: str
    provider_id: str
    issuer: str
    service_subject: str


class AuthentikManagementError(RuntimeError):
    pass


class AuthentikConflictError(AuthentikManagementError):
    pass


class AuthentikUserNotFoundError(AuthentikManagementError):
    """The target user no longer exists in Authentik."""


class AuthentikAdminClient:
    """Least-privilege authentik adapter that never logs response bodies."""

    def __init__(
        self,
        api_url: str,
        api_token: str,
        external_url: str,
        template_client_id: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.external_url = external_url.rstrip("/")
        self.template_client_id = template_client_id
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _json_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                f"{self.api_url}/{path.lstrip('/')}",
                params=params,
                json=json,
            )
        except httpx.HTTPError as error:
            raise AuthentikManagementError("authentik management API is unavailable") from error
        if response.status_code not in expected:
            if response.status_code == 400:
                raise AuthentikConflictError(
                    "authentik rejected the application credential configuration"
                )
            raise AuthentikManagementError(
                f"authentik management API returned status {response.status_code}"
            )
        if response.status_code == 204:
            return {}
        try:
            payload = response.json()
        except ValueError as error:
            raise AuthentikManagementError(
                "authentik management API returned invalid JSON"
            ) from error
        if not isinstance(payload, dict):
            raise AuthentikManagementError("authentik management API returned an invalid object")
        return cast(dict[str, Any], payload)

    async def _provider_by_client_id(self, client_id: str) -> dict[str, Any] | None:
        payload = await self._json_request(
            "GET",
            "/providers/oauth2/",
            params={"client_id": client_id, "page_size": 2},
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise AuthentikManagementError("authentik provider list is invalid")
        typed_results = cast(list[object], raw_results)
        results: list[dict[str, Any]] = [
            cast(dict[str, Any], item) for item in typed_results if isinstance(item, dict)
        ]
        exact = [item for item in results if item.get("client_id") == client_id]
        if len(exact) > 1:
            raise AuthentikManagementError("authentik client identifier is ambiguous")
        return exact[0] if exact else None

    async def _scope_mapping_ids(self, scope_codes: set[str]) -> list[str]:
        payload = await self._json_request(
            "GET",
            "/propertymappings/provider/scope/",
            params={"page_size": 200},
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise AuthentikManagementError("authentik scope mapping list is invalid")
        resolved: dict[str, str] = {}
        for raw_item in cast(list[object], raw_results):
            item = raw_item
            if not isinstance(item, dict):
                continue
            typed_item = cast(dict[str, Any], item)
            scope_name = typed_item.get("scope_name")
            mapping_id = typed_item.get("pk")
            if isinstance(scope_name, str) and isinstance(mapping_id, str):
                resolved[scope_name] = mapping_id
        missing = sorted(scope_codes - resolved.keys())
        if missing:
            raise AuthentikManagementError(
                "authentik lacks required platform scope mappings: " + ", ".join(missing)
            )
        return [resolved[scope] for scope in sorted(scope_codes)]

    @staticmethod
    def _new_secret() -> str:
        return secrets.token_urlsafe(48)

    @staticmethod
    def client_id(application_id: str, environment: str, version: int) -> str:
        return f"{application_id}__{environment}__v{version}"

    @staticmethod
    def provider_name(application_id: str, environment: str, version: int) -> str:
        # authentik derives the client-credentials service-account username from
        # the provider name. Keeping it identical to the versioned client_id
        # preserves the platform's deterministic ``application_id__env__vN``
        # subject mapping used by the ai_hub.identity scope expression.
        return AuthentikAdminClient.client_id(application_id, environment, version)

    @staticmethod
    def application_slug(application_id: str, environment: str, version: int) -> str:
        return f"ai-hub-{application_id}-{environment}-v{version}"

    async def provision(
        self,
        *,
        application_id: str,
        application_name: str,
        environment: str,
        launch_url: str,
        redirect_uris: list[str],
        scopes: list[str],
        version: int,
    ) -> ProvisionedCredential:
        if version < 1:
            raise ValueError("Credential version must be positive")
        client_id = self.client_id(application_id, environment, version)
        if await self._provider_by_client_id(client_id) is not None:
            raise AuthentikConflictError("Application credential already exists in authentik")
        template = await self._provider_by_client_id(self.template_client_id)
        if template is None:
            raise AuthentikManagementError("authentik provider template was not found")
        required_scopes = set(scopes) | {
            "openid",
            "profile",
            "email",
            "offline_access",
            "ai_hub.identity",
        }
        property_mappings = await self._scope_mapping_ids(required_scopes)
        client_secret = self._new_secret()
        provider_payload: dict[str, Any] = {
            "name": self.provider_name(application_id, environment, version),
            "authentication_flow": template.get("authentication_flow"),
            "authorization_flow": template.get("authorization_flow"),
            "invalidation_flow": template.get("invalidation_flow"),
            "property_mappings": property_mappings,
            "client_type": "confidential",
            "client_id": client_id,
            "client_secret": client_secret,
            "access_code_validity": "minutes=1",
            "access_token_validity": "minutes=5",
            "refresh_token_validity": "hours=8",
            "include_claims_in_id_token": True,
            "signing_key": template.get("signing_key"),
            "redirect_uris": [
                {
                    "matching_mode": "strict",
                    "url": uri,
                    "redirect_uri_type": "authorization",
                }
                for uri in redirect_uris
            ],
            "sub_mode": "user_username",
            "issuer_mode": "per_provider",
            "grant_types": [
                "authorization_code",
                "client_credentials",
                "refresh_token",
            ],
        }
        provider = await self._json_request(
            "POST",
            "/providers/oauth2/",
            json=provider_payload,
            expected=(201,),
        )
        provider_id = provider.get("pk")
        if not isinstance(provider_id, int):
            raise AuthentikManagementError("authentik provider identifier is invalid")
        slug = self.application_slug(application_id, environment, version)
        try:
            await self._json_request(
                "POST",
                "/core/applications/",
                json={
                    "name": f"{application_name} ({environment}, credential v{version})",
                    "slug": slug,
                    "provider": provider_id,
                    "backchannel_providers": [],
                    "open_in_new_tab": False,
                    "meta_launch_url": launch_url,
                    "meta_description": ("Managed by AI Hub platform application registration."),
                    "meta_publisher": "AI Hub Platform",
                    "policy_engine_mode": "all",
                },
                expected=(201,),
            )
        except Exception:
            await self._json_request(
                "DELETE",
                f"/providers/oauth2/{provider_id}/",
                expected=(204,),
            )
            raise
        issuer = f"{self.external_url}/application/o/{slug}/"
        return ProvisionedCredential(
            client_id=client_id,
            client_secret=client_secret,
            provider_id=str(provider_id),
            issuer=issuer,
            service_subject=f"ak-{client_id}-client_credentials",
        )

    async def rotate(self, *, client_id: str) -> str:
        provider = await self._provider_by_client_id(client_id)
        if provider is None:
            raise AuthentikManagementError("authentik provider was not found")
        provider_id = provider.get("pk")
        if not isinstance(provider_id, int):
            raise AuthentikManagementError("authentik provider identifier is invalid")
        client_secret = self._new_secret()
        await self._json_request(
            "PATCH",
            f"/providers/oauth2/{provider_id}/",
            json={"client_secret": client_secret},
        )
        return client_secret

    async def revoke(self, *, client_id: str) -> None:
        provider = await self._provider_by_client_id(client_id)
        if provider is None:
            raise AuthentikManagementError("authentik provider was not found")
        provider_id = provider.get("pk")
        if not isinstance(provider_id, int):
            raise AuthentikManagementError("authentik provider identifier is invalid")
        await self._json_request(
            "PATCH",
            f"/providers/oauth2/{provider_id}/",
            json={"client_secret": self._new_secret()},
        )

    async def update_redirects(
        self,
        *,
        client_id: str,
        redirect_uris: list[str],
    ) -> None:
        provider = await self._provider_by_client_id(client_id)
        if provider is None:
            raise AuthentikManagementError("authentik provider was not found")
        provider_id = provider.get("pk")
        if not isinstance(provider_id, int):
            raise AuthentikManagementError("authentik provider identifier is invalid")
        await self._json_request(
            "PATCH",
            f"/providers/oauth2/{provider_id}/",
            json={
                "redirect_uris": [
                    {
                        "matching_mode": "strict",
                        "url": uri,
                        "redirect_uri_type": "authorization",
                    }
                    for uri in redirect_uris
                ]
            },
        )

    async def update_scopes(self, *, client_id: str, scopes: list[str]) -> None:
        provider = await self._provider_by_client_id(client_id)
        if provider is None:
            raise AuthentikManagementError("authentik provider was not found")
        provider_id = provider.get("pk")
        if not isinstance(provider_id, int):
            raise AuthentikManagementError("authentik provider identifier is invalid")
        property_mappings = await self._scope_mapping_ids(
            set(scopes) | {"openid", "profile", "email", "offline_access", "ai_hub.identity"}
        )
        await self._json_request(
            "PATCH",
            f"/providers/oauth2/{provider_id}/",
            json={"property_mappings": property_mappings},
        )

    async def _user_by_username(self, username: str) -> dict[str, Any] | None:
        payload = await self._json_request(
            "GET",
            "/core/users/",
            params={"username": username, "page_size": 2},
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise AuthentikManagementError("authentik user list is invalid")
        typed_results = cast(list[object], raw_results)
        results: list[dict[str, Any]] = [
            cast(dict[str, Any], item) for item in typed_results if isinstance(item, dict)
        ]
        exact = [item for item in results if item.get("username") == username]
        if len(exact) > 1:
            raise AuthentikManagementError("authentik username is ambiguous")
        return exact[0] if exact else None

    async def create_user(
        self,
        *,
        username: str,
        name: str,
        email: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        existing = await self._user_by_username(username)
        if existing is not None:
            raise AuthentikConflictError(f"authentik user '{username}' already exists")
        payload: dict[str, Any] = {
            "username": username,
            "name": name,
            "is_active": True,
            "path": "users",
        }
        if email:
            payload["email"] = email
        user = await self._json_request(
            "POST",
            "/core/users/",
            json=payload,
            expected=(201,),
        )
        if password:
            try:
                await self.set_user_password(username=username, password=password)
            except Exception as original_error:
                # Compensate: remove the orphaned user if password setup fails.
                # A failing cleanup must NOT mask the original error: the
                # caller reports the password failure, and the log records the
                # surviving username so an operator can reconcile the orphan.
                user_id = user.get("pk")
                if isinstance(user_id, int):
                    try:
                        await self._json_request(
                            "DELETE",
                            f"/core/users/{user_id}/",
                            expected=(204,),
                        )
                    except Exception:
                        LOGGER.error(
                            "compensation delete failed for orphaned authentik user '%s' (pk=%s); "
                            "manual cleanup required before retrying creation",
                            username,
                            user_id,
                        )
                raise original_error
        return user

    async def update_user(
        self,
        *,
        username: str,
        name: str | None = None,
        email: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        user = await self._user_by_username(username)
        if user is None:
            raise AuthentikManagementError(f"authentik user '{username}' was not found")
        user_id = user.get("pk")
        if not isinstance(user_id, int):
            raise AuthentikManagementError("authentik user identifier is invalid")
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if email is not None:
            payload["email"] = email
        if is_active is not None:
            payload["is_active"] = is_active
        updated = await self._json_request(
            "PATCH",
            f"/core/users/{user_id}/",
            json=payload,
        )
        return updated

    async def set_authorization_version(self, *, username: str, version: int) -> None:
        # The ai_hub.identity scope reads attributes.authorization_version into
        # the token claims; the SDK rejects a snapshot whose version differs
        # from the claim, so every local version bump must be mirrored here.
        user = await self._user_by_username(username)
        if user is None:
            raise AuthentikManagementError(f"authentik user '{username}' was not found")
        user_id = user.get("pk")
        if not isinstance(user_id, int):
            raise AuthentikManagementError("authentik user identifier is invalid")
        attributes = user.get("attributes")
        merged: dict[str, Any] = (
            dict(cast(dict[str, Any], attributes)) if isinstance(attributes, dict) else {}
        )
        merged["authorization_version"] = version
        await self._json_request(
            "PATCH",
            f"/core/users/{user_id}/",
            json={"attributes": merged},
        )

    async def delete_user(self, *, username: str) -> None:
        user = await self._user_by_username(username)
        if user is None:
            raise AuthentikUserNotFoundError(f"authentik user '{username}' was not found")
        user_id = user.get("pk")
        if not isinstance(user_id, int):
            raise AuthentikManagementError("authentik user identifier is invalid")
        await self._json_request(
            "DELETE",
            f"/core/users/{user_id}/",
            expected=(204,),
        )

    async def list_users(
        self,
        *,
        query: str | None = None,
        is_active: bool | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {"page_size": 100, "page": 1}
        if query:
            params["search"] = query
        if is_active is not None:
            params["is_active"] = is_active
        results: list[dict[str, Any]] = []
        while True:
            payload = await self._json_request(
                "GET",
                "/core/users/",
                params=params,
            )
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise AuthentikManagementError("authentik user list is invalid")
            results.extend(
                cast(dict[str, Any], item)
                for item in cast(list[object], raw_results)
                if isinstance(item, dict)
            )
            pagination = payload.get("pagination")
            if not isinstance(pagination, dict):
                break
            next_page = cast(dict[str, Any], pagination).get("next")
            if not isinstance(next_page, int) or next_page < 1:
                break
            params["page"] = next_page
        return results

    async def set_user_password(self, *, username: str, password: str) -> None:
        user = await self._user_by_username(username)
        if user is None:
            raise AuthentikManagementError(f"authentik user '{username}' was not found")
        user_id = user.get("pk")
        if not isinstance(user_id, int):
            raise AuthentikManagementError("authentik user identifier is invalid")
        await self._json_request(
            "POST",
            f"/core/users/{user_id}/set_password/",
            json={"password": password},
            expected=(204,),
        )
