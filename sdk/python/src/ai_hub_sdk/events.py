from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class CloudEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specversion: str = "1.0"
    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str
    type: str
    subject: str | None = None
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    datacontenttype: str = "application/json"
    dataschema: str | None = None
    data: dict[str, Any]
