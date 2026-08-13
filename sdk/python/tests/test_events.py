from datetime import UTC, datetime
from uuid import UUID

from ai_hub_sdk import CloudEvent, EventActor


def test_cloud_event_contract() -> None:
    event = CloudEvent(
        source="urn:ai-hub:application:quality",
        type="quality.case.created.v1",
        subject="quality-case/QC-001",
        time=datetime(2026, 8, 12, tzinfo=UTC),
        dataschema="https://contracts.example/quality-case.v1.schema.json",
        producer_application_id="quality",
        event_version=1,
        aggregate_version=1,
        source_sequence=1,
        object_type="quality_case",
        actor=EventActor(type="user", id="user-001"),
        data_classification="internal",
        data={"case_id": "QC-001"},
    )

    assert isinstance(event.id, UUID)
    assert event.specversion == "1.0"
    assert event.datacontenttype == "application/json"
    assert event.data == {"case_id": "QC-001"}
