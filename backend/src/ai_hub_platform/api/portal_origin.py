from urllib.parse import urlsplit

from fastapi import Request

from ai_hub_platform.api.errors import ApiError


def resolve_portal_request_origin(request: Request) -> str:
    settings = request.app.state.settings
    forwarded_protocol = _single_header(request.headers.get("x-forwarded-proto"))
    forwarded_host = _single_header(request.headers.get("x-forwarded-host"))
    origin = _origin_from_protocol_host(
        forwarded_protocol or request.url.scheme,
        forwarded_host or request.headers.get("host"),
    )
    if origin not in settings.portal_allowed_origins():
        raise ApiError(421, "unknown_request_origin", "Portal request Origin is not allowed")
    return origin


def request_origin_header_matches(request: Request) -> bool:
    value = request.headers.get("origin")
    if value is None:
        return False
    try:
        parsed = urlsplit(_single_header(value) or "")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        origin = _origin_from_protocol_host(parsed.scheme, parsed.netloc)
        return origin == resolve_portal_request_origin(request)
    except (ApiError, ValueError):
        return False


def _origin_from_protocol_host(protocol: str, host: str | None) -> str:
    if protocol not in {"http", "https"} or not host:
        raise ApiError(421, "invalid_request_origin", "Portal request Origin is invalid")
    try:
        parsed = urlsplit(f"{protocol}://{host}")
        hostname = parsed.hostname
        parsed_port = parsed.port
    except ValueError as error:
        raise ApiError(421, "invalid_request_origin", "Portal request Origin is invalid") from error
    if hostname is None or parsed.path or parsed.query or parsed.fragment:
        raise ApiError(421, "invalid_request_origin", "Portal request Origin is invalid")
    if (protocol, parsed_port) in {("http", 80), ("https", 443)}:
        parsed_port = None
    port = f":{parsed_port}" if parsed_port is not None else ""
    return f"{protocol}://{hostname}{port}"


def _single_header(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or "," in normalized or "\r" in normalized or "\n" in normalized:
        raise ApiError(421, "invalid_request_origin", "Portal request Origin is invalid")
    return normalized
