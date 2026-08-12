from dataclasses import dataclass
from enum import StrEnum


class IntegrationCapability(StrEnum):
    API_CLIENT = "API_CLIENT"
    EVENT_PUBLISHER = "EVENT_PUBLISHER"
    EVENT_CONSUMER = "EVENT_CONSUMER"
    PROJECTION_SOURCE = "PROJECTION_SOURCE"


@dataclass(frozen=True, slots=True)
class ApplicationRegistration:
    application_id: str
    name: str
    capabilities: frozenset[IntegrationCapability] = frozenset({IntegrationCapability.API_CLIENT})
