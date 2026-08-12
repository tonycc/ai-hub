from ai_hub_sdk import CloudEvent


def test_cloud_event_defaults() -> None:
    event = CloudEvent(
        source="urn:ai-hub:application:quality",
        type="quality.case.created.v1",
        subject="quality-case/QC-001",
        data={"case_id": "QC-001"},
    )

    assert event.specversion == "1.0"
    assert event.datacontenttype == "application/json"
    assert event.data == {"case_id": "QC-001"}
