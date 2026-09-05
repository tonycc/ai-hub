#!/usr/bin/env python3
"""Render the validated Mac mini endpoint Compose override."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from urllib.parse import urlsplit

PRIVATE_NETWORKS = tuple(
    IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
ENV_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-([^}]*))?\}")
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class EndpointError(ValueError):
    pass


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EndpointError(f"{path}:{number} is not a KEY=value entry")
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None:
            raise EndpointError(f"{path}:{number} has an invalid key")
        values[key] = value

    def expand(value: str, stack: tuple[str, ...] = ()) -> str:
        def replace(match: re.Match[str]) -> str:
            key, fallback = match.groups()
            if key in stack:
                chain = " -> ".join((*stack, key))
                raise EndpointError(f"recursive environment reference: {chain}")
            selected = values.get(key) or fallback
            if selected is None:
                raise EndpointError(f"unresolved environment reference: {key}")
            return expand(selected, (*stack, key))

        previous = None
        while previous != value:
            previous = value
            value = ENV_REFERENCE.sub(replace, value)
        if "${" in value:
            raise EndpointError("unsupported environment expansion in runtime env")
        return value

    return {key: expand(value, (key,)) for key, value in values.items()}


def csv(value: str | None, name: str) -> list[str]:
    if value is None or not value.strip():
        raise EndpointError(f"{name} is required")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise EndpointError(f"{name} cannot contain control characters")
    items = [item.strip() for item in value.split(",")]
    if any(not item for item in items):
        raise EndpointError(f"{name} cannot contain empty entries")
    if len(set(items)) != len(items):
        raise EndpointError(f"{name} cannot contain duplicate entries")
    return items


def private_ipv4(value: str, name: str) -> str:
    try:
        address = IPv4Address(value)
    except ValueError as error:
        raise EndpointError(f"{name} must contain RFC1918 IPv4 addresses: {value}") from error
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise EndpointError(f"{name} must contain RFC1918 IPv4 addresses: {value}")
    return value


def port(value: str | None, name: str, fallback: int) -> int:
    try:
        parsed = int(value or fallback)
    except ValueError as error:
        raise EndpointError(f"{name} must be a valid port") from error
    if not 1 <= parsed <= 65535:
        raise EndpointError(f"{name} must be between 1 and 65535")
    return parsed


def origin(value: str, name: str, expected_port: int) -> tuple[str, str]:
    if value != value.lower():
        raise EndpointError(f"{name} must use lowercase")
    parsed = urlsplit(value)
    try:
        parsed_port = parsed.port
    except ValueError as error:
        raise EndpointError(f"{name} contains an invalid port: {value}") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise EndpointError(f"{name} must contain HTTPS Origins without a path: {value}")
    effective_port = parsed_port or 443
    if effective_port != expected_port:
        raise EndpointError(f"{name} must use port {expected_port}: {value}")
    hostname = parsed.hostname
    if ":" in hostname:
        raise EndpointError(f"{name} does not support IPv6: {value}")
    try:
        IPv4Address(hostname)
    except ValueError:
        labels = hostname.split(".")
        invalid_label = any(DNS_LABEL.fullmatch(label) is None for label in labels)
        if len(labels) < 2 or len(hostname) > 253 or invalid_label:
            raise EndpointError(f"{name} contains an invalid DNS name: {value}") from None
    else:
        private_ipv4(hostname, name)
    normalized = f"https://{hostname}" + (f":{effective_port}" if effective_port != 443 else "")
    return normalized, hostname


def endpoint_config(values: dict[str, str]) -> dict[str, object]:
    server_ip = private_ipv4(values.get("AI_HUB_SERVER_IP", ""), "AI_HUB_SERVER_IP")
    bind_addresses = [
        private_ipv4(value, "AI_HUB_BIND_ADDRESSES")
        for value in csv(values.get("AI_HUB_BIND_ADDRESSES") or server_ip, "AI_HUB_BIND_ADDRESSES")
    ]
    if bind_addresses[0] != server_ip:
        raise EndpointError("AI_HUB_SERVER_IP must equal the first AI_HUB_BIND_ADDRESSES entry")
    platform_port = port(
        values.get("AI_HUB_PLATFORM_HTTPS_PORT"), "AI_HUB_PLATFORM_HTTPS_PORT", 443
    )
    identity_port = port(values.get("AI_HUB_AUTH_HTTPS_PORT"), "AI_HUB_AUTH_HTTPS_PORT", 8443)
    if platform_port == identity_port:
        raise EndpointError("platform and identity ports must differ")
    platform_raw = values.get("AI_HUB_PLATFORM_ORIGINS") or f"https://{server_ip}:{platform_port}"
    identity_raw = values.get("AI_HUB_IDENTITY_ORIGINS") or f"https://{server_ip}:{identity_port}"
    platform_origins = [
        origin(value, "AI_HUB_PLATFORM_ORIGINS", platform_port)
        for value in csv(platform_raw, "AI_HUB_PLATFORM_ORIGINS")
    ]
    identity_origins = [
        origin(value, "AI_HUB_IDENTITY_ORIGINS", identity_port)
        for value in csv(identity_raw, "AI_HUB_IDENTITY_ORIGINS")
    ]
    platform_hosts = list(dict.fromkeys(host for _, host in platform_origins))
    identity_hosts = list(dict.fromkeys(host for _, host in identity_origins))
    for address in bind_addresses:
        if address not in platform_hosts:
            raise EndpointError(f"AI_HUB_PLATFORM_ORIGINS is missing bind address {address}")
        if address not in identity_hosts:
            raise EndpointError(f"AI_HUB_IDENTITY_ORIGINS is missing bind address {address}")
    return {
        "bind_addresses": bind_addresses,
        "platform_port": platform_port,
        "identity_port": identity_port,
        "platform_origins": [value for value, _ in platform_origins],
        "identity_origins": [value for value, _ in identity_origins],
        "platform_hosts": platform_hosts,
        "identity_hosts": identity_hosts,
        "certificate_dns_names": list(
            dict.fromkeys(
                host
                for host in (*platform_hosts, *identity_hosts)
                if not _is_ipv4(host)
            )
        ),
    }


def _is_ipv4(value: str) -> bool:
    try:
        IPv4Address(value)
    except ValueError:
        return False
    return True


def host_rule(hosts: list[str]) -> str:
    return "(" + " || ".join(f"Host(`{host}`)" for host in hosts) + ")"


def render(config: dict[str, object]) -> str:
    mappings = [
        mapping
        for address in config["bind_addresses"]
        for mapping in (
            f"{address}:{config['platform_port']}:443",
            f"{address}:{config['identity_port']}:8443",
        )
    ]
    lines = [
        "# Generated by render-endpoint-compose.py. Do not edit.",
        "services:",
        "  traefik:",
        "    ports: !override",
        *(f"      - {json.dumps(mapping)}" for mapping in mappings),
        "    environment:",
        f"      AI_HUB_PLATFORM_HOST_RULE: {json.dumps(host_rule(config['platform_hosts']))}",
        f"      AI_HUB_IDENTITY_HOST_RULE: {json.dumps(host_rule(config['identity_hosts']))}",
        "",
    ]
    return "\n".join(lines)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
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
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--print",
        dest="print_field",
        choices=("bind-addresses", "platform-origins", "identity-origins", "certificate-dns-names"),
    )
    args = parser.parse_args()
    config = endpoint_config(parse_env(args.env_file))
    if args.print_field:
        key = args.print_field.replace("-", "_")
        print(",".join(config[key]))
        return
    if args.output is None:
        parser.error("--output is required unless --print is used")
    atomic_write(args.output, render(config))
    print(args.output)


if __name__ == "__main__":
    try:
        main()
    except (EndpointError, OSError) as error:
        raise SystemExit(f"render-endpoint-compose: {error}") from None
