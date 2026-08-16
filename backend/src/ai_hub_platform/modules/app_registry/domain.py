from dataclasses import dataclass
from enum import StrEnum


class IntegrationCapability(StrEnum):
    API_CLIENT = "API_CLIENT"
    DATA_INGEST = "DATA_INGEST"


@dataclass(frozen=True, slots=True)
class ApplicationRegistration:
    application_id: str
    name: str
    capabilities: frozenset[IntegrationCapability] = frozenset({IntegrationCapability.API_CLIENT})
