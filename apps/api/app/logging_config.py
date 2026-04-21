"""Structured JSON logging.

Ported from SMB-MetaPattern. Emits one JSON object per line to stdout,
redacts PII on the fly via `PiiRedactionFilter`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

from .pii_filter import PiiRedactionFilter, redact_structure


class JsonFormatter(logging.Formatter):
    _STANDARD_ATTRS = frozenset(
        {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs",
            "pathname", "process", "processName", "relativeCreated",
            "stack_info", "exc_info", "exc_text", "thread", "threadName",
            "message", "taskName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._STANDARD_ATTRS or value is None:
                continue
            try:
                log_obj[key] = redact_structure(value)
            except Exception:
                log_obj[key] = str(value)
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, default=str)


def setup_logging(level: str = "INFO") -> None:
    log_level = os.environ.get("LOG_LEVEL", level).upper()
    numeric_level = getattr(logging, log_level, logging.INFO)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    formatter = JsonFormatter()
    pii_filter = PiiRedactionFilter()

    root = logging.getLogger()
    root.setLevel(numeric_level)
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)
    handler.setFormatter(formatter)
    handler.addFilter(pii_filter)
    root.addHandler(handler)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_handler = logging.StreamHandler(sys.stdout)
        uv_handler.setLevel(numeric_level)
        uv_handler.setFormatter(formatter)
        uv_handler.addFilter(pii_filter)
        uv_logger.addHandler(uv_handler)
        uv_logger.setLevel(numeric_level)
