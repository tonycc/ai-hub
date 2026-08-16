from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

AlertStatus = Literal["PENDING", "FIRING", "RECOVERED"]
DIRECT_HTTP_OPENER = build_opener(ProxyHandler({}))


class MonitorError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Observation:
    rule_id: str
    severity: str
    route: str
    runbook: str
    object_id: str
    active: bool
    summary: str
    request_id: str | None = None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.rule_id}\0{self.object_id}".encode()).hexdigest()[:24]


@dataclass(slots=True)
class AlertState:
    first_observed_at: str
    last_observed_at: str
    status: AlertStatus
    notified_status: str | None


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MonitorError(f"Cannot load JSON configuration: {path}") from error
    if not isinstance(value, dict):
        raise MonitorError(f"JSON configuration must be an object: {path}")
    return cast(dict[str, Any], value)


def _request_json(url: str, *, monitor_token: str, timeout: float = 5.0) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "X-AI-Hub-Monitor-Token": monitor_token,
            "User-Agent": "ai-hub-monitor/1",
        },
    )
    try:
        with DIRECT_HTTP_OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise MonitorError(f"Internal operations endpoint failed: {url}") from error
    if not isinstance(payload, dict):
        raise MonitorError("Internal operations response must be an object")
    return cast(dict[str, Any], payload)


def _request_ready(url: str, *, timeout: float = 5.0) -> tuple[bool, str | None]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ai-hub-monitor/1"})
    try:
        with DIRECT_HTTP_OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            payload_value = json.loads(response.read())
            request_id = response.headers.get("X-Request-ID")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return False, None
    if not isinstance(payload_value, dict):
        return False, request_id
    payload = cast(dict[str, Any], payload_value)
    return payload.get("status") == "ok", request_id


def _probe_http(url: str, *, host: str, timeout: float = 5.0) -> bool:
    request = Request(url, headers={"Host": host, "User-Agent": "ai-hub-monitor/1"})
    try:
        with DIRECT_HTTP_OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError):
        return False


def _probe_url(url: str, *, timeout: float = 5.0) -> bool:
    request = Request(url, headers={"User-Agent": "ai-hub-monitor/1"})
    try:
        with DIRECT_HTTP_OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError, ValueError):
        return False


def probe_application_entries(operations: dict[str, Any]) -> None:
    raw_entries = operations.get("application_entries")
    if not isinstance(raw_entries, list):
        raise MonitorError("Operations summary application entries are invalid")
    for raw_entry in cast(list[object], raw_entries):
        if not isinstance(raw_entry, dict):
            raise MonitorError("Operations summary application entry is invalid")
        entry = cast(dict[str, Any], raw_entry)
        if entry.get("environment_status") != "ACTIVE":
            continue
        health_url = entry.get("health_url")
        if not isinstance(health_url, str):
            entry["status"] = "CRITICAL"
            entry["reason"] = "Application health URL is missing"
        elif _probe_url(health_url):
            entry["status"] = "HEALTHY"
            entry["reason"] = "Active application health probe passed"
        else:
            entry["status"] = "CRITICAL"
            entry["reason"] = "Active application health probe failed"


def _edge_probe_url(edge_base_url: str, path: object) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise MonitorError("HTTP probe path is invalid")
    return edge_base_url.rstrip("/") + path


def rules_by_id(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_rules_value = config.get("rules")
    if not isinstance(raw_rules_value, list):
        raise MonitorError("Alert rules must contain a rules list")
    raw_rules = cast(list[object], raw_rules_value)
    result: dict[str, dict[str, Any]] = {}
    for raw_rule_value in raw_rules:
        if not isinstance(raw_rule_value, dict):
            raise MonitorError("Alert rule entry is invalid")
        rule = cast(dict[str, Any], raw_rule_value)
        if not isinstance(rule.get("id"), str):
            raise MonitorError("Alert rule entry is invalid")
        rule_id = str(rule["id"])
        if rule_id in result:
            raise MonitorError(f"Duplicate alert rule: {rule_id}")
        result[rule_id] = rule
    return result


def _observation(
    rule: dict[str, Any],
    *,
    object_id: str,
    active: bool,
    summary: str,
    request_id: str | None = None,
) -> Observation:
    return Observation(
        rule_id=str(rule["id"]),
        severity=str(rule["severity"]),
        route=str(rule["route"]),
        runbook=str(rule["runbook"]),
        object_id=object_id,
        active=active,
        summary=summary,
        request_id=request_id,
    )


def _target_number(targets: dict[str, Any], path: str) -> float:
    value: object = targets
    for part in path.split("."):
        if not isinstance(value, dict):
            raise MonitorError(f"Operating target path is invalid: {path}")
        typed: dict[str, Any] = cast(dict[str, Any], value)  # pyright: ignore[reportUnnecessaryCast]
        value = typed.get(part)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MonitorError(f"Operating target is not numeric: {path}")
    return float(value)


def _condition_threshold(rule: dict[str, Any], targets: dict[str, Any]) -> float:
    raw_condition = rule.get("condition")
    if not isinstance(raw_condition, dict):
        raise MonitorError(f"Alert rule condition is invalid: {rule.get('id')}")
    condition = cast(dict[str, Any], raw_condition)
    target_path = condition.get("target_path")
    if isinstance(target_path, str):
        return _target_number(targets, target_path)
    value = condition.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MonitorError(f"Alert rule threshold is not numeric: {rule.get('id')}")
    return float(value)


def evaluate_observations(
    rules: dict[str, dict[str, Any]],
    *,
    readiness: bool,
    readiness_request_id: str | None,
    http_probes: dict[str, bool] | None = None,
    operations: dict[str, Any],
    backup_age_minutes: float | None,
    targets: dict[str, Any] | None = None,
) -> list[Observation]:
    operating_targets = targets or {"recovery": {"rpo_minutes": 60}}
    observations = [
        _observation(
            rules["platform-api-unready"],
            object_id="ai-hub-platform",
            active=not readiness,
            summary=(
                "Platform readiness endpoint is unavailable"
                if not readiness
                else "Platform is ready"
            ),
            request_id=readiness_request_id,
        )
    ]
    for rule_id, probe_ok in sorted((http_probes or {}).items()):
        rule = rules[rule_id]
        observations.append(
            _observation(
                rule,
                object_id=rule_id.removesuffix("-unready"),
                active=not probe_ok,
                summary=(
                    f"{rule_id} HTTP probe failed"
                    if not probe_ok
                    else f"{rule_id} probe passed"
                ),
            )
        )
    for raw_entry in cast(list[object], operations.get("application_entries", [])):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(dict[str, Any], raw_entry)
        object_id = (
            f"{entry.get('application_id', 'unknown')}:"
            f"{entry.get('environment', 'unknown')}"
        )
        critical = entry.get("status") == "CRITICAL"
        observations.append(
            _observation(
                rules["application-entry-critical"],
                object_id=object_id,
                active=critical,
                summary=str(entry.get("reason", "Application entry status changed")),
            )
        )
    backup_rule = rules["backup-rpo-breached"]
    threshold = _condition_threshold(backup_rule, operating_targets)
    backup_active = backup_age_minutes is None or backup_age_minutes > threshold
    backup_summary = (
        "No verified off-host backup was found"
        if backup_age_minutes is None
        else f"Newest verified off-host backup is {backup_age_minutes:.1f} minutes old"
    )
    observations.append(
        _observation(
            backup_rule,
            object_id="off-host-backup",
            active=backup_active,
            summary=backup_summary,
        )
    )
    return observations


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def transition_alerts(
    observations: Sequence[Observation],
    previous: dict[str, AlertState],
    rules: dict[str, dict[str, Any]],
    *,
    now: datetime,
) -> tuple[dict[str, AlertState], list[dict[str, Any]]]:
    current: dict[str, AlertState] = {}
    notifications: list[dict[str, Any]] = []
    now_iso = now.isoformat()
    observed_fingerprints: set[str] = set()
    for observation in observations:
        fingerprint = observation.fingerprint
        observed_fingerprints.add(fingerprint)
        prior = previous.get(fingerprint)
        if observation.active:
            recurring = prior is not None and prior.status != "RECOVERED"
            first_at = prior.first_observed_at if recurring and prior else now_iso
            elapsed = max(0.0, (now - _parse_time(first_at)).total_seconds())
            required = int(rules[observation.rule_id]["for_seconds"])
            status: AlertStatus = "FIRING" if elapsed >= required else "PENDING"
            notified_status = prior.notified_status if recurring and prior else None
            state = AlertState(first_at, now_iso, status, notified_status)
            if status == "FIRING" and notified_status != "FIRING":
                notifications.append(_notification(observation, status, first_at, now_iso))
                state.notified_status = "FIRING"
            current[fingerprint] = state
        elif prior and prior.status in {"PENDING", "FIRING"}:
            if prior.notified_status == "FIRING":
                notifications.append(
                    _notification(
                        observation,
                        "RECOVERED",
                        prior.first_observed_at,
                        now_iso,
                    )
                )
            current[fingerprint] = AlertState(
                prior.first_observed_at,
                now_iso,
                "RECOVERED",
                "RECOVERED",
            )
    for fingerprint, prior in previous.items():
        if fingerprint not in observed_fingerprints and prior.status != "RECOVERED":
            current[fingerprint] = prior
    return current, notifications


def _notification(
    observation: Observation,
    status: Literal["FIRING", "RECOVERED"],
    first_observed_at: str,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "fingerprint": observation.fingerprint,
        "rule_id": observation.rule_id,
        "severity": observation.severity,
        "status": status,
        "first_observed_at": first_observed_at,
        "observed_at": observed_at,
        "object": observation.object_id,
        "summary": observation.summary,
        "request_id": observation.request_id,
        "route": observation.route,
        "runbook": observation.runbook,
    }


def _load_state(path: Path) -> dict[str, AlertState]:
    if not path.exists():
        return {}
    raw = _load_json_object(path)
    result: dict[str, AlertState] = {}
    for fingerprint, value in raw.items():
        if not isinstance(value, dict):
            raise MonitorError("Monitor state entry is invalid")
        entry = cast(dict[str, Any], value)
        result[fingerprint] = AlertState(
            first_observed_at=str(entry["first_observed_at"]),
            last_observed_at=str(entry["last_observed_at"]),
            status=cast(AlertStatus, str(entry["status"])),
            notified_status=(
                str(entry["notified_status"])
                if entry.get("notified_status") is not None
                else None
            ),
        )
    return result


def _write_state(path: Path, state: dict[str, AlertState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(
            {key: asdict(value) for key, value in sorted(state.items())},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def backup_age_minutes(directory: Path, now: datetime) -> float | None:
    newest: datetime | None = None
    for path in directory.glob("ai-hub-backup-*.tar.aesgcm"):
        sidecar = path.with_suffix(path.suffix + ".sha256")
        receipt_path = path.with_suffix(path.suffix + ".verified.json")
        if not sidecar.is_file() or not receipt_path.is_file():
            continue
        fields = sidecar.read_text(encoding="utf-8").split()
        if len(fields) != 2 or fields[1] != path.name:
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        archive_sha256 = digest.hexdigest()
        if archive_sha256 != fields[0]:
            continue
        try:
            receipt_value = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(receipt_value, dict):
            continue
        receipt = cast(dict[str, Any], receipt_value)
        if (
            receipt.get("schema_version") != 1
            or receipt.get("verified") is not True
            or receipt.get("archive") != path.name
            or receipt.get("archive_sha256") != archive_sha256
            or receipt.get("backup_id")
            != path.name.removesuffix(".tar.aesgcm")
            or receipt.get("storage_class") != "off-host"
        ):
            continue
        try:
            created_at = datetime.fromisoformat(str(receipt["created_at"]))
            verified_at = datetime.fromisoformat(str(receipt["verified_at"]))
            filename_created_at = datetime.strptime(
                path.name.split("-")[3], "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=UTC)
        except (KeyError, ValueError, IndexError):
            continue
        if created_at.tzinfo is None or verified_at.tzinfo is None:
            continue
        created_at = created_at.astimezone(UTC)
        verified_at = verified_at.astimezone(UTC)
        if (
            abs((created_at - filename_created_at).total_seconds()) >= 1
            or verified_at < created_at
            or created_at > now
            or verified_at > now
        ):
            continue
        newest = created_at if newest is None or created_at > newest else newest
    return None if newest is None else max(0.0, (now - newest).total_seconds() / 60)


def _send_webhook(url: str, payload: dict[str, Any], secret: str | None) -> None:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "ai-hub-monitor/1"}
    if secret:
        headers["X-AI-Hub-Signature-256"] = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=5.0) as response:  # noqa: S310
            if response.status < 200 or response.status >= 300:
                raise MonitorError(f"Alert webhook returned HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError) as error:
        raise MonitorError("Alert webhook delivery failed") from error


def run_check(args: argparse.Namespace) -> dict[str, Any]:
    monitor_token = os.environ.get("AI_HUB_MONITOR_TOKEN", "")
    if not monitor_token:
        raise MonitorError("AI_HUB_MONITOR_TOKEN is required")
    rules_config = _load_json_object(Path(args.rules))
    targets = _load_json_object(Path(args.targets))
    rules = rules_by_id(rules_config)
    target_routes = cast(dict[str, Any], targets.get("alert_routes", {}))
    unknown_routes = {str(rule["route"]) for rule in rules.values()} - set(target_routes)
    if unknown_routes:
        raise MonitorError(f"Alert rules reference unknown routes: {sorted(unknown_routes)}")
    now = datetime.now(UTC)
    readiness, request_id = _request_ready(args.readiness_url)
    try:
        operations = _request_json(args.operations_url, monitor_token=monitor_token)
    except MonitorError:
        if readiness:
            raise
        operations = cast(
            dict[str, Any],
            {"application_entries": []},
        )
    probe_application_entries(operations)
    observations = evaluate_observations(
        rules,
        readiness=readiness,
        readiness_request_id=request_id,
        http_probes={
            "identity-unready": _probe_http(
                _edge_probe_url(args.edge_base_url, rules["identity-unready"].get("path")),
                host=args.identity_host,
            ),
            "portal-unready": _probe_http(
                _edge_probe_url(args.edge_base_url, rules["portal-unready"].get("path")),
                host=args.platform_host,
            ),
        },
        operations=operations,
        backup_age_minutes=backup_age_minutes(Path(args.backup_directory), now),
        targets=targets,
    )
    state_path = Path(args.state_file)
    state, notifications = transition_alerts(
        observations,
        _load_state(state_path),
        rules,
        now=now,
    )
    webhook_url = os.environ.get("AI_HUB_ALERT_WEBHOOK_URL")
    webhook_secret = os.environ.get("AI_HUB_ALERT_WEBHOOK_SECRET")
    if notifications and not webhook_url:
        raise MonitorError("AI_HUB_ALERT_WEBHOOK_URL is required when an alert changes state")
    for notification in notifications:
        route = cast(dict[str, Any], target_routes[str(notification["route"])])
        enriched = {
            **notification,
            "owner": route["primary"],
            "backup_owner": route["backup"],
            "acknowledge_minutes": route["acknowledge_minutes"],
        }
        _send_webhook(str(webhook_url), enriched, webhook_secret)
    _write_state(state_path, state)
    return {
        "checked_at": now.isoformat(),
        "observation_count": len(observations),
        "firing_count": sum(value.status == "FIRING" for value in state.values()),
        "pending_count": sum(value.status == "PENDING" for value in state.values()),
        "notifications_sent": len(notifications),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate AI Hub production alert rules")
    parser.add_argument(
        "--operations-url",
        default="http://127.0.0.1:18080/internal/operations/summary",
    )
    parser.add_argument(
        "--readiness-url",
        default="http://127.0.0.1:18080/health/ready",
    )
    parser.add_argument("--rules", default="deploy/operations/alert-rules.json")
    parser.add_argument("--targets", default="deploy/operations/production-targets.json")
    parser.add_argument("--backup-directory", required=True)
    parser.add_argument("--edge-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--identity-host", default="auth.localhost")
    parser.add_argument("--platform-host", default="platform.localhost")
    parser.add_argument("--state-file", default="/var/lib/ai-hub-monitor/state.json")
    return parser


def main() -> None:
    try:
        result = run_check(build_parser().parse_args())
    except MonitorError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
