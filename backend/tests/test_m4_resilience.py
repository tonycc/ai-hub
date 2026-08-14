from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from ai_hub_platform.api.operations import production_targets_response
from ai_hub_platform.operations.resilience import (
    HttpSample,
    evaluate_load,
    percentile,
    run_http_load,
)
from ai_hub_platform.operations.targets import (
    ProductionTargetsError,
    SloTargets,
    load_production_targets,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TARGETS_PATH = PROJECT_ROOT / "deploy/operations/production-targets.json"


def _targets(*, minimum_requests: int = 1000, minimum_rps: float = 20) -> SloTargets:
    return SloTargets(
        monthly_availability_percent=99.5,
        public_api_p95_ms=500,
        public_api_p99_ms=1500,
        minimum_test_rps=minimum_rps,
        minimum_test_requests=minimum_requests,
        maximum_server_error_percent=1,
        event_backlog_warning=100,
        event_backlog_critical=1000,
        event_recovery_minutes=15,
    )


def test_runtime_targets_are_loaded_from_the_single_approved_document() -> None:
    load_production_targets.cache_clear()
    targets = load_production_targets(str(TARGETS_PATH))

    assert targets.deployment_tier == "STANDARD_SINGLE_NODE"
    assert targets.profile == "standard-events"
    assert targets.slo.minimum_test_requests == 1000
    assert targets.slo.minimum_test_rps == 20
    assert targets.slo.event_backlog_warning == 100
    assert targets.slo.event_backlog_critical == 1000
    assert targets.deployment_topology == "single-host-docker-compose"
    assert targets.off_host_backup_required is True
    assert targets.recovery.rpo_minutes == 60
    assert targets.retention.audit_days == 365
    assert {route.route_key for route in targets.alert_routes} == {
        "application-integration",
        "data-recovery",
        "identity-security",
        "platform-runtime",
    }


def test_runtime_targets_have_a_safe_read_only_portal_contract() -> None:
    targets = load_production_targets(str(TARGETS_PATH))
    response = production_targets_response(targets).model_dump()

    assert response["configuration_mode"] == "CONFIG_AS_CODE"
    assert response["editable"] is False
    assert response["source"] == "deploy/operations/production-targets.json"
    assert response["deployment"]["tier"] == "STANDARD_SINGLE_NODE"
    assert response["recovery"] == {
        "rpo_minutes": 60,
        "rto_minutes": 120,
        "projection_rto_minutes": 240,
        "backup_interval_minutes": 60,
    }
    assert "production_targets_path" not in response


def test_runtime_targets_reject_inconsistent_backlog_thresholds(tmp_path: Path) -> None:
    document = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    document["slo"]["event_backlog_warning"] = 1001
    path = tmp_path / "invalid-targets.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    load_production_targets.cache_clear()

    with pytest.raises(ProductionTargetsError, match="backlog targets"):
        load_production_targets(str(path))


def test_runtime_targets_reject_an_unsafe_backup_interval(tmp_path: Path) -> None:
    document = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    document["recovery"]["backup_interval_minutes"] = 61
    path = tmp_path / "invalid-recovery-targets.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    load_production_targets.cache_clear()

    with pytest.raises(ProductionTargetsError, match="Backup interval"):
        load_production_targets(str(path))


def test_runtime_targets_reject_unknown_configuration_fields(tmp_path: Path) -> None:
    document = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    document["slo"]["unreviewed_limit"] = 42
    path = tmp_path / "unknown-field-targets.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    load_production_targets.cache_clear()

    with pytest.raises(ProductionTargetsError, match="Unexpected.*unreviewed_limit"):
        load_production_targets(str(path))


def test_load_evidence_uses_nearest_rank_percentiles_and_fails_closed() -> None:
    assert percentile([1, 2, 3, 4, 100], 95) == 100
    samples = [HttpSample(elapsed_ms=25, status_code=200) for _ in range(1000)]
    passed = evaluate_load(
        samples,
        requested=1000,
        scheduled_rps=25,
        wall_seconds=40,
        targets=_targets(),
    )
    assert passed.passed is True
    assert passed.achieved_rps == 25

    samples[-1] = HttpSample(
        elapsed_ms=1600,
        status_code=None,
        error_type="ReadTimeout",
    )
    failed = evaluate_load(
        samples,
        requested=1000,
        scheduled_rps=25,
        wall_seconds=40,
        targets=_targets(),
    )
    assert failed.passed is False
    assert failed.transport_errors == 1


@pytest.mark.asyncio
async def test_async_load_runner_reuses_auth_without_exposing_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"application_id": "standalone-example"})

    evidence = await run_http_load(
        url="https://platform.test/platform-api/v1/applications/standalone-example",
        bearer_token="sensitive-load-token",
        requested=10,
        scheduled_rps=1000,
        concurrency=5,
        timeout_seconds=1,
        targets=_targets(minimum_requests=10, minimum_rps=1),
        transport=httpx.MockTransport(handler),
    )

    assert evidence.passed is True
    assert len(requests) == 10
    assert all(
        request.headers["authorization"] == "Bearer sensitive-load-token" for request in requests
    )
    assert "sensitive-load-token" not in json.dumps(evidence.targets)


def test_health_probe_releases_database_transaction_before_external_call() -> None:
    source = (PROJECT_ROOT / "backend/src/ai_hub_platform/api/platform.py").read_text(
        encoding="utf-8"
    )

    rollback = source.index("await session.rollback()")
    probe = source.index("await registry.probe_health(")
    record = source.index("await registry.record_health(")
    assert rollback < probe < record
