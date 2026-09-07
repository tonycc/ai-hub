#!/usr/bin/env python3
"""Create a candidate runtime.env containing only endpoint-related changes."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

MANAGED_KEYS = (
    "AI_HUB_SERVER_IP",
    "AI_HUB_BIND_ADDRESSES",
    "AI_HUB_PLATFORM_ORIGINS",
    "AI_HUB_PLATFORM_DEFAULT_ORIGIN",
    "AI_HUB_IDENTITY_ORIGINS",
    "AI_HUB_IDENTITY_DEFAULT_ORIGIN",
    "AI_HUB_PORTAL_OIDC_REDIRECT_URI",
    "AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URI",
    "AI_HUB_PORTAL_OIDC_REDIRECT_URIS",
    "AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URIS",
    "AI_HUB_OIDC_ISSUER",
    "AI_HUB_PORTAL_OIDC_ISSUER",
    "AI_HUB_AUTHENTIK_EXTERNAL_URL",
    "AI_HUB_AUTHENTIK_BRAND_DOMAIN",
    "AI_HUB_BRAND_ICON_URL",
    "AI_HUB_PUBLIC_PLATFORM_BASE_URL",
    "AI_HUB_PUBLIC_IDENTITY_BASE_URL",
    "AI_HUB_PORTAL_EXTERNAL_URL",
)


def origin(value: str, expected_port: int, label: str) -> str:
    parsed = urlsplit(value)
    try:
        actual_port = parsed.port or 443
    except ValueError as error:
        raise ValueError(f"{label} contains an invalid port") from error
    if (
        value != value.lower()
        or parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or actual_port != expected_port
    ):
        raise ValueError(f"{label} must be a lowercase HTTPS Origin on port {expected_port}")
    return f"https://{parsed.hostname}" + (f":{actual_port}" if actual_port != 443 else "")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bind-address", action="append", required=True)
    parser.add_argument("--platform-origin", action="append", required=True)
    parser.add_argument("--identity-origin", action="append", required=True)
    parser.add_argument("--platform-default-origin")
    parser.add_argument("--identity-default-origin")
    args = parser.parse_args()

    source_lines = args.env_file.read_text(encoding="utf-8").splitlines()
    port_values = {}
    for line in source_lines:
        if line.startswith("AI_HUB_PLATFORM_HTTPS_PORT="):
            port_values["platform"] = int(line.split("=", 1)[1])
        elif line.startswith("AI_HUB_AUTH_HTTPS_PORT="):
            port_values["identity"] = int(line.split("=", 1)[1])
    platform_port = port_values.get("platform", 443)
    identity_port = port_values.get("identity", 8443)
    platform_origins = list(
        dict.fromkeys(
            origin(value, platform_port, "platform Origin")
            for value in args.platform_origin
        )
    )
    identity_origins = list(
        dict.fromkeys(
            origin(value, identity_port, "identity Origin")
            for value in args.identity_origin
        )
    )
    if len(platform_origins) != len(args.platform_origin) or len(identity_origins) != len(
        args.identity_origin
    ):
        raise ValueError("Origin lists cannot contain duplicates")
    platform_default = origin(
        args.platform_default_origin or platform_origins[0],
        platform_port,
        "platform default Origin",
    )
    identity_default = origin(
        args.identity_default_origin or identity_origins[0],
        identity_port,
        "identity default Origin",
    )
    if platform_default not in platform_origins or identity_default not in identity_origins:
        raise ValueError("default Origins must be present in their allowlists")
    redirects = [f"{value}/auth/callback" for value in platform_origins]
    logouts = [f"{value}/" for value in platform_origins]
    identity_netloc = urlsplit(identity_default).netloc
    updates = {
        "AI_HUB_SERVER_IP": args.bind_address[0],
        "AI_HUB_BIND_ADDRESSES": ",".join(args.bind_address),
        "AI_HUB_PLATFORM_ORIGINS": ",".join(platform_origins),
        "AI_HUB_PLATFORM_DEFAULT_ORIGIN": platform_default,
        "AI_HUB_IDENTITY_ORIGINS": ",".join(identity_origins),
        "AI_HUB_IDENTITY_DEFAULT_ORIGIN": identity_default,
        "AI_HUB_PORTAL_OIDC_REDIRECT_URI": f"{platform_default}/auth/callback",
        "AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URI": f"{platform_default}/",
        "AI_HUB_PORTAL_OIDC_REDIRECT_URIS": ",".join(redirects),
        "AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URIS": ",".join(logouts),
        "AI_HUB_OIDC_ISSUER": f"{identity_default}/application/o/ai-hub/",
        "AI_HUB_PORTAL_OIDC_ISSUER": f"{identity_default}/application/o/ai-hub-portal/",
        "AI_HUB_AUTHENTIK_EXTERNAL_URL": identity_default,
        "AI_HUB_AUTHENTIK_BRAND_DOMAIN": identity_netloc,
        "AI_HUB_BRAND_ICON_URL": f"{platform_default}/ai-hub-icon.svg",
        "AI_HUB_PUBLIC_PLATFORM_BASE_URL": platform_default,
        "AI_HUB_PUBLIC_IDENTITY_BASE_URL": identity_default,
        "AI_HUB_PORTAL_EXTERNAL_URL": platform_default,
    }

    seen: set[str] = set()
    result = []
    for line in source_lines:
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        key = match.group(1) if match else None
        if key in updates:
            if key in seen:
                raise ValueError(f"runtime env contains duplicate {key}")
            result.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            result.append(line)
    for key in MANAGED_KEYS:
        if key not in seen:
            result.append(f"{key}={updates[key]}")
    atomic_write(args.output, "\n".join(result) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"configure-macmini-endpoints: {error}") from None
