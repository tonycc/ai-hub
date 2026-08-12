from ai_hub_platform.modules.app_registry.domain import (
    ApplicationRegistration,
    IntegrationCapability,
)


def test_application_defaults_to_api_client_only() -> None:
    application = ApplicationRegistration(application_id="quality", name="质量异常")

    assert application.capabilities == frozenset({IntegrationCapability.API_CLIENT})
