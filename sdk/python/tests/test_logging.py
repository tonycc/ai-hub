from ai_hub_sdk import json_log_config


def test_json_log_config_preserves_application_loggers_at_info() -> None:
    config = json_log_config()

    assert config["disable_existing_loggers"] is False
    assert config["root"]["level"] == "INFO"
    assert config["handlers"]["stdout"]["stream"] == "ext://sys.stdout"
    assert config["loggers"]["uvicorn.access"]["level"] == "WARNING"
