from datetime import UTC, datetime

import pytest
from ai_hub_platform.modules.projection.service import (
    ProjectionContractError,
    validate_registered_event,
)
from ai_hub_sdk import CloudEvent, EventActor


def registered_event(**changes: object) -> CloudEvent:
    record_id = "30000000-0000-4000-8000-000000000001"
    values: dict[str, object] = {
        "source": "urn:ai-hub:application:standalone-example",
        "type": "company.example.record.changed.v1",
        "subject": f"example-record/{record_id}",
        "time": datetime(2026, 8, 12, tzinfo=UTC),
        "dataschema": "https://contracts.example/event.v1.schema.json",
        "producer_application_id": "standalone-example",
        "event_version": 1,
        "aggregate_version": 2,
        "source_sequence": 5,
        "object_type": "example_record",
        "actor": EventActor(type="service", id="publisher"),
        "data_classification": "internal",
        "data": {
            "record_id": record_id,
            "name": "Record",
            "state": "ACTIVE",
            "owner_subject": "owner",
        },
    }
    values.update(changes)
    return CloudEvent.model_validate(values)


def test_registered_event_contract_accepts_changed_and_deleted_facts() -> None:
    changed = registered_event()
    deleted = registered_event(
        type="company.example.record.deleted.v1",
        data={"record_id": "30000000-0000-4000-8000-000000000001"},
    )

    validate_registered_event(changed)
    validate_registered_event(deleted)


def test_projection_rejects_unregistered_event_type() -> None:
    with pytest.raises(ProjectionContractError, match="not registered"):
        validate_registered_event(registered_event(type="company.example.record.hidden.v1"))


def test_projection_rejects_subject_and_record_id_mismatch() -> None:
    with pytest.raises(ProjectionContractError, match="subject does not match"):
        validate_registered_event(
            registered_event(subject="example-record/30000000-0000-4000-8000-000000000002")
        )


def test_projection_rejects_unregistered_producer() -> None:
    with pytest.raises(ProjectionContractError, match="not registered"):
        validate_registered_event(
            registered_event(
                source="urn:ai-hub:application:another-app",
                producer_application_id="another-app",
            )
        )
