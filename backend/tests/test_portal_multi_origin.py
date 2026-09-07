import pytest
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.api.portal_origin import (
    request_origin_header_matches,
    resolve_portal_request_origin,
)
from ai_hub_platform.config import Settings
from fastapi import FastAPI
from starlette.requests import Request


def test_request_origin_accepts_only_an_exact_forwarded_origin() -> None:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        portal_oidc_redirect_uri="http://192.168.33.20:8088/auth/callback",
        portal_oidc_logout_redirect_uri="http://192.168.33.20:8088/",
        platform_origins=(
            "http://192.168.33.20:8088,http://platform.example.com:8088"
        ),
    )
    request = _request(
        settings,
        host="platform-api:8000",
        forwarded_protocol="http",
        forwarded_host="platform.example.com:8088",
    )

    assert resolve_portal_request_origin(request) == "http://platform.example.com:8088"

    with pytest.raises(ApiError, match="not allowed"):
        resolve_portal_request_origin(_request(settings, host="unknown.example.com:8088"))


def test_origin_header_must_match_the_forwarded_portal_origin() -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    matching = _request(settings, host="platform.localhost:8088", origin="http://platform.localhost:8088")
    mismatched = _request(settings, host="platform.localhost:8088", origin="http://evil.localhost:8088")

    assert request_origin_header_matches(matching) is True
    assert request_origin_header_matches(mismatched) is False


def _request(
    settings: Settings,
    *,
    host: str,
    forwarded_protocol: str | None = None,
    forwarded_host: str | None = None,
    origin: str | None = None,
) -> Request:
    headers = [(b"host", host.encode())]
    if forwarded_protocol:
        headers.append((b"x-forwarded-proto", forwarded_protocol.encode()))
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    app = FastAPI()
    app.state.settings = settings
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/auth/login",
            "raw_path": b"/auth/login",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("platform-api", 8000),
            "app": app,
            "state": {},
        }
    )
