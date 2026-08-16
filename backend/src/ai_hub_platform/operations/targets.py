from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any, cast


class ProductionTargetsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SloTargets:
    monthly_availability_percent: float
    public_api_p95_ms: float
    public_api_p99_ms: float
    minimum_test_rps: float
    minimum_test_requests: int
    maximum_server_error_percent: float


@dataclass(frozen=True, slots=True)
class HaUpgradeTriggers:
    availability_percent: float
    rpo_minutes: int
    rto_minutes: int
    sustained_rps: float


@dataclass(frozen=True, slots=True)
class ServiceWindowTargets:
    days: tuple[str, ...]
    start: str
    end: str
    planned_maintenance_notice_hours: int


@dataclass(frozen=True, slots=True)
class RecoveryTargets:
    rpo_minutes: int
    rto_minutes: int
    backup_interval_minutes: int


@dataclass(frozen=True, slots=True)
class RetentionTargets:
    audit_days: int
    notification_days: int
    portal_session_days_after_expiry: int
    conformance_days_after_expiry: int
    backup_hourly_count: int
    backup_daily_days: int


@dataclass(frozen=True, slots=True)
class AlertRouteTargets:
    route_key: str
    primary: str
    backup: str
    acknowledge_minutes: int


@dataclass(frozen=True, slots=True)
class ProductionTargets:
    schema_version: int
    timezone: str
    service_window: ServiceWindowTargets
    deployment_tier: str
    profile: str
    deployment_topology: str
    off_host_backup_required: bool
    slo: SloTargets
    recovery: RecoveryTargets
    retention: RetentionTargets
    alert_routes: tuple[AlertRouteTargets, ...]
    ha_upgrade_triggers: HaUpgradeTriggers


def _object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ProductionTargetsError(f"Production targets field must be an object: {key}")
    return cast(dict[str, Any], value)


def _exact_keys(
    value: dict[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    context: str,
) -> None:
    actual = frozenset(value)
    missing = required - actual
    unexpected = actual - required - optional
    if missing:
        raise ProductionTargetsError(
            f"Production targets field is missing in {context}: {sorted(missing)[0]}"
        )
    if unexpected:
        raise ProductionTargetsError(
            f"Unexpected production targets field in {context}: {sorted(unexpected)[0]}"
        )


def _number(parent: dict[str, Any], key: str) -> float:
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProductionTargetsError(f"Production target must be numeric: {key}")
    return float(value)


def _integer(parent: dict[str, Any], key: str) -> int:
    value = parent.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductionTargetsError(f"Production target must be an integer: {key}")
    return value


def _string(parent: dict[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProductionTargetsError(f"Production target must be a string: {key}")
    return value


def _boolean(parent: dict[str, Any], key: str) -> bool:
    value = parent.get(key)
    if not isinstance(value, bool):
        raise ProductionTargetsError(f"Production target must be a boolean: {key}")
    return value


def _string_tuple(parent: dict[str, Any], key: str) -> tuple[str, ...]:
    value = parent.get(key)
    if not isinstance(value, list) or not value:
        raise ProductionTargetsError(f"Production target must be a non-empty list: {key}")
    items: list[str] = []
    for item in cast(list[Any], value):
        if not isinstance(item, str) or not item.strip():
            raise ProductionTargetsError(f"Production target list must contain strings: {key}")
        items.append(item)
    return tuple(items)


def _positive_integer(parent: dict[str, Any], key: str) -> int:
    value = _integer(parent, key)
    if value <= 0:
        raise ProductionTargetsError(f"Production target must be positive: {key}")
    return value


def _alert_routes(parent: dict[str, Any]) -> tuple[AlertRouteTargets, ...]:
    if not parent:
        raise ProductionTargetsError("At least one alert route is required")
    routes: list[AlertRouteTargets] = []
    for route_key in sorted(parent):
        route = _object(parent, route_key)
        _exact_keys(
            route,
            required=frozenset({"primary", "backup", "acknowledge_minutes"}),
            context=f"alert_routes.{route_key}",
        )
        routes.append(
            AlertRouteTargets(
                route_key=route_key,
                primary=_string(route, "primary"),
                backup=_string(route, "backup"),
                acknowledge_minutes=_positive_integer(route, "acknowledge_minutes"),
            )
        )
    return tuple(routes)


@lru_cache(maxsize=8)
def load_production_targets(path_value: str) -> ProductionTargets:
    path = Path(path_value)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProductionTargetsError(f"Cannot read production targets: {path}") from error
    if not isinstance(document, dict):
        raise ProductionTargetsError("Production targets document must be an object")
    root = cast(dict[str, Any], document)
    _exact_keys(
        root,
        required=frozenset(
            {
                "schema_version",
                "timezone",
                "service_window",
                "deployment_tier",
                "slo",
                "recovery",
                "retention",
                "alert_routes",
                "ha_upgrade_triggers",
            }
        ),
        optional=frozenset({"$schema"}),
        context="document",
    )
    if root.get("schema_version") != 1:
        raise ProductionTargetsError("Unsupported production targets schema_version")
    service_window = _object(root, "service_window")
    deployment = _object(root, "deployment_tier")
    slo = _object(root, "slo")
    recovery = _object(root, "recovery")
    retention = _object(root, "retention")
    alert_routes = _object(root, "alert_routes")
    triggers = _object(root, "ha_upgrade_triggers")
    _exact_keys(
        service_window,
        required=frozenset({"days", "start", "end", "planned_maintenance_notice_hours"}),
        context="service_window",
    )
    _exact_keys(
        deployment,
        required=frozenset({"id", "profile", "topology", "off_host_backup_required"}),
        context="deployment_tier",
    )
    _exact_keys(
        slo,
        required=frozenset(
            {
                "monthly_availability_percent",
                "public_api_p95_ms",
                "public_api_p99_ms",
                "minimum_test_rps",
                "minimum_test_requests",
                "maximum_server_error_percent",
            }
        ),
        context="slo",
    )
    _exact_keys(
        recovery,
        required=frozenset(
            {"rpo_minutes", "rto_minutes", "backup_interval_minutes"}
        ),
        context="recovery",
    )
    _exact_keys(
        retention,
        required=frozenset(
            {
                "audit_days",
                "notification_days",
                "portal_session_days_after_expiry",
                "conformance_days_after_expiry",
                "backup_hourly_count",
                "backup_daily_days",
            }
        ),
        context="retention",
    )
    _exact_keys(
        triggers,
        required=frozenset({"availability_percent", "rpo_minutes", "rto_minutes", "sustained_rps"}),
        context="ha_upgrade_triggers",
    )
    targets = ProductionTargets(
        schema_version=1,
        timezone=_string(root, "timezone"),
        service_window=ServiceWindowTargets(
            days=_string_tuple(service_window, "days"),
            start=_string(service_window, "start"),
            end=_string(service_window, "end"),
            planned_maintenance_notice_hours=_positive_integer(
                service_window, "planned_maintenance_notice_hours"
            ),
        ),
        deployment_tier=_string(deployment, "id"),
        profile=_string(deployment, "profile"),
        deployment_topology=_string(deployment, "topology"),
        off_host_backup_required=_boolean(deployment, "off_host_backup_required"),
        slo=SloTargets(
            monthly_availability_percent=_number(slo, "monthly_availability_percent"),
            public_api_p95_ms=_integer(slo, "public_api_p95_ms"),
            public_api_p99_ms=_integer(slo, "public_api_p99_ms"),
            minimum_test_rps=_integer(slo, "minimum_test_rps"),
            minimum_test_requests=_integer(slo, "minimum_test_requests"),
            maximum_server_error_percent=_number(slo, "maximum_server_error_percent"),
        ),
        recovery=RecoveryTargets(
            rpo_minutes=_positive_integer(recovery, "rpo_minutes"),
            rto_minutes=_positive_integer(recovery, "rto_minutes"),
            backup_interval_minutes=_positive_integer(recovery, "backup_interval_minutes"),
        ),
        retention=RetentionTargets(
            audit_days=_positive_integer(retention, "audit_days"),
            notification_days=_positive_integer(retention, "notification_days"),
            portal_session_days_after_expiry=_positive_integer(
                retention, "portal_session_days_after_expiry"
            ),
            conformance_days_after_expiry=_positive_integer(
                retention, "conformance_days_after_expiry"
            ),
            backup_hourly_count=_positive_integer(retention, "backup_hourly_count"),
            backup_daily_days=_positive_integer(retention, "backup_daily_days"),
        ),
        alert_routes=_alert_routes(alert_routes),
        ha_upgrade_triggers=HaUpgradeTriggers(
            availability_percent=_number(triggers, "availability_percent"),
            rpo_minutes=_integer(triggers, "rpo_minutes"),
            rto_minutes=_integer(triggers, "rto_minutes"),
            sustained_rps=_integer(triggers, "sustained_rps"),
        ),
    )
    if targets.deployment_tier != "STANDARD_SINGLE_NODE":
        raise ProductionTargetsError("Unsupported production deployment tier")
    if targets.profile not in {"base-access"}:
        raise ProductionTargetsError("Unsupported production deployment profile")
    if targets.timezone != "Asia/Shanghai":
        raise ProductionTargetsError("Unsupported production timezone")
    if targets.deployment_topology != "single-host-docker-compose":
        raise ProductionTargetsError("Unsupported production deployment topology")
    if not targets.off_host_backup_required:
        raise ProductionTargetsError("Off-host backup must be required")
    allowed_days = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
    if (
        len(set(targets.service_window.days)) != len(targets.service_window.days)
        or not set(targets.service_window.days) <= allowed_days
    ):
        raise ProductionTargetsError("Service-window days are invalid")
    try:
        if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", targets.service_window.start):
            raise ValueError
        if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", targets.service_window.end):
            raise ValueError
        window_start = time.fromisoformat(targets.service_window.start)
        window_end = time.fromisoformat(targets.service_window.end)
    except ValueError as error:
        raise ProductionTargetsError("Service-window times are invalid") from error
    if window_start >= window_end:
        raise ProductionTargetsError("Service-window times are inconsistent")
    if not 99 <= targets.slo.monthly_availability_percent <= 100:
        raise ProductionTargetsError("Availability target must be between 99 and 100")
    if not 0 < targets.slo.public_api_p95_ms <= targets.slo.public_api_p99_ms:
        raise ProductionTargetsError("Public API latency targets are inconsistent")
    if targets.slo.minimum_test_rps <= 0 or targets.slo.minimum_test_requests < 100:
        raise ProductionTargetsError("Load-test size and rate targets are below the minimum")
    if not 0 <= targets.slo.maximum_server_error_percent <= 10:
        raise ProductionTargetsError("Server error target must be a percentage")
    if targets.recovery.backup_interval_minutes > targets.recovery.rpo_minutes:
        raise ProductionTargetsError("Backup interval cannot exceed the RPO target")
    if not 99 <= targets.ha_upgrade_triggers.availability_percent <= 100:
        raise ProductionTargetsError("HA availability trigger must be between 99 and 100")
    if targets.ha_upgrade_triggers.availability_percent <= targets.slo.monthly_availability_percent:
        raise ProductionTargetsError("HA availability trigger must exceed the baseline SLO")
    if targets.ha_upgrade_triggers.rpo_minutes < 0:
        raise ProductionTargetsError("HA RPO trigger cannot be negative")
    if targets.ha_upgrade_triggers.rpo_minutes >= targets.recovery.rpo_minutes:
        raise ProductionTargetsError("HA RPO trigger must improve the baseline RPO")
    if not 0 < targets.ha_upgrade_triggers.rto_minutes < targets.recovery.rto_minutes:
        raise ProductionTargetsError("HA RTO trigger must improve the baseline RTO")
    if targets.ha_upgrade_triggers.sustained_rps <= 0:
        raise ProductionTargetsError("HA throughput trigger must be positive")
    return targets
