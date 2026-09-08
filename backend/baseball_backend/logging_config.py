"""Structured (JSON) logging for API and workers."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_STANDARD_ATTRS = {
    "name",
    "msg",
    "args",
    "created",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "exc_info",
    "exc_text",
    "thread",
    "threadName",
    "message",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            if value is not None:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(*, level: str = "INFO", json_logs: bool = True) -> None:
    """Configure root logging once for the process."""
    root = logging.getLogger()
    if getattr(root, "_baseball_logging_configured", False):
        root.setLevel(level.upper())
        return

    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Keep uvicorn access/error loggers aligned with our formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    root._baseball_logging_configured = True  # type: ignore[attr-defined]
