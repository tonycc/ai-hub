from __future__ import annotations

from typing import Any


def json_log_config() -> dict[str, Any]:
    """Return a stdout-only logging baseline suitable for container runtimes."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "message": {"format": "%(message)s"},
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "message",
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["stdout"], "level": "INFO"},
        "loggers": {
            "uvicorn": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
            "uvicorn.error": {
                "handlers": ["stdout"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["stdout"],
                "level": "WARNING",
                "propagate": False,
            },
        },
    }
