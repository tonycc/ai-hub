from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_hub_sdk import (
    CloudEvent,
    EventActor,
    ExampleRecordSnapshotItem,
    example_record_snapshot_checksum,
)
from pydantic import ValidationError


def event(**changes: object) -> CloudEvent:
    values: dict[str, object] = {
        "source": "urn:ai-hub:application:standalone-example",
        "type": "company.example.record.changed.v1",
        "subject": "example-record/30000000-0000-4000-8000-000000000001",
        "time": datetime(2026, 8, 12, tzinfo=UTC),
        "dataschema": "https://contracts.example/event.v1.schema.json",
        "producer_application_id": "standalone-example",
        "event_version": 1,
        "aggregate_version": 2,
        "source_sequence": 5,
        "object_type": "example_record",
        "trace_id": "trace-001",
        "actor": EventActor(type="service", id="publisher"),
        "data_classification": "internal",
        "data": {"record_id": "30000000-0000-4000-8000-000000000001"},
    }
    values.update(changes)
    return CloudEvent.model_validate(values)


def test_cloud_event_requires_source_to_match_registered_producer() -> None:
    with pytest.raises(ValidationError, match="source must identify"):
        event(source="urn:ai-hub:application:unregistered")


def test_cloud_event_round_trip_preserves_ordering_and_idempotency_keys() -> None:
    original = event(id=UUID("40000000-0000-4000-8000-000000000001"))

    restored = CloudEvent.model_validate_json(original.model_dump_json(exclude_none=False))

    assert restored.id == original.id
    assert restored.source_sequence == 5
    assert restored.aggregate_version == 2
    assert restored.trace_id == "trace-001"


def test_snapshot_checksum_is_order_independent_but_content_sensitive() -> None:
    first = ExampleRecordSnapshotItem(
        record_id=UUID("30000000-0000-4000-8000-000000000001"),
        name="First",
        state="ACTIVE",
        owner_subject="owner",
        aggregate_version=1,
        updated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    second = ExampleRecordSnapshotItem(
        record_id=UUID("30000000-0000-4000-8000-000000000002"),
        name="Second",
        state="ACTIVE",
        owner_subject="owner",
        aggregate_version=3,
        updated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert example_record_snapshot_checksum(
        [first, second],
        producer_application_id="standalone-example",
        watermark=3,
    ) == (
        example_record_snapshot_checksum(
            [second, first],
            producer_application_id="standalone-example",
            watermark=3,
        )
    )
    assert example_record_snapshot_checksum(
        [first],
        producer_application_id="standalone-example",
        watermark=3,
    ) != (
        example_record_snapshot_checksum(
            [second],
            producer_application_id="standalone-example",
            watermark=3,
        )
    )


def test_snapshot_checksum_binds_source_and_watermark() -> None:
    record = ExampleRecordSnapshotItem(
        record_id=UUID("30000000-0000-4000-8000-000000000001"),
        name="Record",
        state="ACTIVE",
        owner_subject="owner",
        aggregate_version=1,
        updated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    baseline = example_record_snapshot_checksum(
        [record],
        producer_application_id="standalone-example",
        watermark=3,
    )

    assert baseline != example_record_snapshot_checksum(
        [record],
        producer_application_id="another-app",
        watermark=3,
    )
    assert baseline != example_record_snapshot_checksum(
        [record],
        producer_application_id="standalone-example",
        watermark=4,
    )
