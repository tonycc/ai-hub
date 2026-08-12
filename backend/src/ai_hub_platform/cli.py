import uvicorn
from ai_hub_sdk import json_log_config


def run() -> None:
    uvicorn.run(
        "ai_hub_platform.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config=json_log_config(),
    )
