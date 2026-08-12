import uvicorn


def run() -> None:
    uvicorn.run(
        "ai_hub_platform.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
