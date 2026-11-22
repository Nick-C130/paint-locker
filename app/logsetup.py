"""Logging: one readable format for Paint Locker and uvicorn, level from LOG_LEVEL (default info)."""

from __future__ import annotations

import logging
import logging.config

from . import config

LEVELS = {"critical", "error", "warning", "info", "debug"}


def level() -> str:
    return config.LOG_LEVEL if config.LOG_LEVEL in LEVELS else "info"


class _Rename(logging.Filter):
    """uvicorn logs normal start-up lines under "uvicorn.error", which reads like a failure."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        return True


def configure() -> dict:
    lvl = level().upper()
    cfg = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "datefmt": "%Y-%m-%d %H:%M:%S"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain", "stream": "ext://sys.stdout", "filters": ["rename"]}},
        "filters": {"rename": {"()": _Rename}},
        "root": {"handlers": ["console"], "level": lvl},
        "loggers": {
            "paintlocker": {"level": lvl},
            "uvicorn": {"level": lvl},
            "uvicorn.error": {"level": lvl},
            "uvicorn.access": {"level": "WARNING"},  # our middleware logs requests with timings instead
            "alembic": {"level": "INFO" if lvl == "DEBUG" else "WARNING"},
            "sqlalchemy.engine": {"level": "WARNING"},
        },
    }
    logging.config.dictConfig(cfg)
    if config.LOG_LEVEL not in LEVELS:
        logging.getLogger("paintlocker").warning("LOG_LEVEL=%r isn't one of %s; using info", config.LOG_LEVEL, sorted(LEVELS))
    return cfg
