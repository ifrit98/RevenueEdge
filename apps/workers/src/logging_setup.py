"""JSON logging for worker processes. Thin version of apps/api logging_config."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class _Formatter(logging.Formatter):
    _STD = frozenset({
        "name", "msg", "args", "created", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs",
        "pathname", "process", "processName", "relativeCreated",
        "stack_info", "exc_info", "exc_text", "thread", "threadName",
        "message", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._STD or value is None:
                continue
            obj[key] = value
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)


def setup_logging(level: str = "INFO") -> None:
    log_level = os.environ.get("LOG_LEVEL", level).upper()
    numeric = getattr(logging, log_level, logging.INFO)
    if not isinstance(numeric, int):
        numeric = logging.INFO
    root = logging.getLogger()
    root.setLevel(numeric)
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter())
    root.addHandler(handler)
