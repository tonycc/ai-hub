import uvicorn


def run() -> None:
    uvicorn.run(
        "standalone_app.main:app",
        host="0.0.0.0",
        port=8100,
        reload=False,
    )
