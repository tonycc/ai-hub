from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventActor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["user", "service", "system"]
    id: str = Field(min_length=1, max_length=255)


class CloudEvent(BaseModel):
    """The versioned public event envelope used by registered applications."""

    model_config = ConfigDict(extra="forbid")

    specversion: Literal["1.0"] = "1.0"
    id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1, max_length=500)
    type: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=500)
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    datacontenttype: Literal["application/json"] = "application/json"
    dataschema: str = Field(min_length=1, max_length=500)
    producer_application_id: str = Field(
        pattern=r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$"
    )
    event_version: int = Field(ge=1)
    aggregate_version: int = Field(ge=1)
    source_sequence: int = Field(ge=1)
    object_type: str = Field(min_length=1, max_length=100)
    trace_id: str | None = Field(default=None, max_length=128)
    actor: EventActor
    data_classification: Literal["public", "internal", "confidential", "restricted"]
    data: dict[str, Any]

    @model_validator(mode="after")
    def validate_source_and_subject(self) -> CloudEvent:
        expected_source = f"urn:ai-hub:application:{self.producer_application_id}"
        if self.source != expected_source:
            raise ValueError("source must identify producer_application_id")
        if "/" not in self.subject:
            raise ValueError("subject must contain an object type and stable identifier")
        return self


class ExampleRecordSnapshotItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: UUID
    name: str = Field(min_length=1, max_length=200)
    state: str = Field(min_length=1, max_length=50)
    owner_subject: str = Field(min_length=1, max_length=255)
    aggregate_version: int = Field(ge=1)
    updated_at: datetime


class ExampleRecordSnapshot(BaseModel):
    """A consistent source snapshot paired with its Outbox watermark."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_id: UUID = Field(default_factory=uuid4)
    producer_application_id: str = Field(
        pattern=r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    watermark: int = Field(ge=0)
    records: list[ExampleRecordSnapshotItem]
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


def example_record_snapshot_checksum(
    records: Iterable[ExampleRecordSnapshotItem],
    *,
    producer_application_id: str,
    watermark: int,
) -> str:
    """Return the canonical checksum for snapshot identity, watermark, and records."""

    canonical_records = [
        record.model_dump(mode="json")
        for record in sorted(records, key=lambda item: str(item.record_id))
    ]
    payload = json.dumps(
        {
            "schema_version": 1,
            "producer_application_id": producer_application_id,
            "watermark": watermark,
            "records": canonical_records,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()
