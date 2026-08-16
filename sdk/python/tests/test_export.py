"""Unit tests for incremental export helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ai_hub_sdk import (
    EXPORT_SCOPE,
    ExportContractError,
    ExportRecord,
    PayloadContract,
    TokenValidationError,
    VerifiedToken,
    allocate_next_version,
    assert_versions_monotonic,
    paginate_export_records,
    require_export_scope,
)


def test_allocate_next_version_and_pagination() -> None:
    assert allocate_next_version(0) == 1
    assert allocate_next_version(41) == 42
    records = [
        ExportRecord(object_id="a", operation="upsert", version=1, payload={"name": "a"}),
        ExportRecord(object_id="b", operation="upsert", version=2, payload={"name": "b"}),
        ExportRecord(object_id="a", operation="delete", version=3, payload=None),
    ]
    page = paginate_export_records(
        records,
        object_type="example_record",
        payload_contract_version="example_record.v1",
        since_version=1,
        limit=1,
        stream_high_watermark=3,
    )
    assert len(page.records) == 1
    assert page.records[0].version == 2
    assert page.has_more is True
    assert page.high_watermark == 3


def test_assert_versions_monotonic_and_payload_contract() -> None:
    assert_versions_monotonic(
        [
            ExportRecord(object_id="a", operation="upsert", version=1, payload={"name": "a"}),
            ExportRecord(object_id="b", operation="delete", version=2, payload=None),
        ]
    )
    with pytest.raises(ExportContractError, match="strictly increasing"):
        assert_versions_monotonic(
            [
                ExportRecord(object_id="a", operation="upsert", version=2, payload={"n": 1}),
                ExportRecord(object_id="b", operation="upsert", version=2, payload={"n": 2}),
            ]
        )
    contract = PayloadContract(
        object_type="example_record",
        contract_version="example_record.v1",
        allowed_keys=frozenset({"name", "state", "owner_subject"}),
    )
    contract.validate_payload({"name": "x", "state": "ACTIVE", "owner_subject": "u"})
    with pytest.raises(ExportContractError, match="undeclared"):
        contract.validate_payload({"name": "x", "secret": "no"})


def test_require_export_scope() -> None:
    now = int(datetime.now(UTC).timestamp())
    token = VerifiedToken(
        subject="svc",
        issuer="https://id.test/",
        audience=("ai-hub-platform",),
        expires_at=now + 300,
        issued_at=now,
        scopes=frozenset({EXPORT_SCOPE, "ai_hub.identity"}),
        actor_type="service",
        application_id="ai-hub-platform",
        authorization_version=1,
        preferred_username=None,
        display_name=None,
        email=None,
        claims={},
    )
    require_export_scope(token)
    bare = VerifiedToken(
        subject="svc",
        issuer="https://id.test/",
        audience=("ai-hub-platform",),
        expires_at=now + 300,
        issued_at=now,
        scopes=frozenset({"ai_hub.identity"}),
        actor_type="service",
        application_id="standalone-example",
        authorization_version=1,
        preferred_username=None,
        display_name=None,
        email=None,
        claims={},
    )
    with pytest.raises(TokenValidationError, match=EXPORT_SCOPE):
        require_export_scope(bare)
