"""stdlib-only structured logging for the simple-servicenow-mcp package.

All output goes to stderr (stdout is reserved for the JSON-RPC stream when the
server is using stdio transport). Configurable via env:

    SN_LOG_LEVEL  = DEBUG | INFO | WARNING | ERROR        (default INFO)
    SN_LOG_FORMAT = text | json                            (default text)
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_LOGRECORD_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)


class _JsonFormatter(logging.Formatter):
    """One JSON object per log record. Structured `extra=` fields are merged in."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install a single stderr handler on the package logger.

    Idempotent: repeat calls replace the existing handler rather than stacking.
    Library loggers (httpx, mcp) are left at WARNING to avoid console spam.
    """
    pkg_logger = logging.getLogger("simple_servicenow_mcp")
    pkg_logger.handlers.clear()
    pkg_logger.propagate = False

    handler = logging.StreamHandler(sys.stderr)
    if fmt.lower() == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s | %(message)s", datefmt="%H:%M:%S"
            )
        )
    pkg_logger.addHandler(handler)
    pkg_logger.setLevel(level.upper())

    # Quiet noisy transitive loggers.
    for noisy in ("httpx", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel("WARNING")
