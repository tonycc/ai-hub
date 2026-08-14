from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast


class ProductionTargetsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SloTargets:
    public_api_p95_ms: float
    public_api_p99_ms: float
    minimum_test_rps: float
    minimum_test_requests: int
    maximum_server_error_percent: float
    event_backlog_warning: int
    event_backlog_critical: int
    event_recovery_minutes: int


@dataclass(frozen=True, slots=True)
class HaUpgradeTriggers:
    availability_percent: float
    rpo_minutes: int
    rto_minutes: int
    sustained_rps: float


@dataclass(frozen=True, slots=True)
class ProductionTargets:
    deployment_tier: str
    profile: str
    slo: SloTargets
    ha_upgrade_triggers: HaUpgradeTriggers


def _object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ProductionTargetsError(f"Production targets field must be an object: {key}")
    return cast(dict[str, Any], value)


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
    if root.get("schema_version") != 1:
        raise ProductionTargetsError("Unsupported production targets schema_version")
    deployment = _object(root, "deployment_tier")
    slo = _object(root, "slo")
    triggers = _object(root, "ha_upgrade_triggers")
    targets = ProductionTargets(
        deployment_tier=_string(deployment, "id"),
        profile=_string(deployment, "profile"),
        slo=SloTargets(
            public_api_p95_ms=_number(slo, "public_api_p95_ms"),
            public_api_p99_ms=_number(slo, "public_api_p99_ms"),
            minimum_test_rps=_number(slo, "minimum_test_rps"),
            minimum_test_requests=_integer(slo, "minimum_test_requests"),
            maximum_server_error_percent=_number(
                slo, "maximum_server_error_percent"
            ),
            event_backlog_warning=_integer(slo, "event_backlog_warning"),
            event_backlog_critical=_integer(slo, "event_backlog_critical"),
            event_recovery_minutes=_integer(slo, "event_recovery_minutes"),
        ),
        ha_upgrade_triggers=HaUpgradeTriggers(
            availability_percent=_number(triggers, "availability_percent"),
            rpo_minutes=_integer(triggers, "rpo_minutes"),
            rto_minutes=_integer(triggers, "rto_minutes"),
            sustained_rps=_number(triggers, "sustained_rps"),
        ),
    )
    if targets.deployment_tier != "STANDARD_SINGLE_NODE":
        raise ProductionTargetsError("Unsupported production deployment tier")
    if targets.profile not in {"base-access", "standard-events"}:
        raise ProductionTargetsError("Unsupported production deployment profile")
    if not 0 < targets.slo.public_api_p95_ms <= targets.slo.public_api_p99_ms:
        raise ProductionTargetsError("Public API latency targets are inconsistent")
    if targets.slo.minimum_test_rps <= 0 or targets.slo.minimum_test_requests <= 0:
        raise ProductionTargetsError("Load-test size and rate targets must be positive")
    if not 0 <= targets.slo.maximum_server_error_percent <= 100:
        raise ProductionTargetsError("Server error target must be a percentage")
    if not 0 < targets.slo.event_backlog_warning < targets.slo.event_backlog_critical:
        raise ProductionTargetsError("Event backlog targets are inconsistent")
    if targets.slo.event_recovery_minutes <= 0:
        raise ProductionTargetsError("Event recovery target must be positive")
    return targets
