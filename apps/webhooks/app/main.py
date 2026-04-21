"""Revenue Edge Webhooks — FastAPI entrypoint.

Accepts signed provider webhooks (Retell voice + SMS for MVP), verifies,
canonicalizes, enqueues to `re-api /internal/queue/enqueue`, returns fast.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

from fastapi import FastAPI

from .retell import router as retell_router
from .settings import get_settings


def _setup_logging(level: str = "INFO") -> None:
    import logging as _logging

    lvl = os.environ.get("LOG_LEVEL", level).upper()
    numeric = getattr(_logging, lvl, _logging.INFO)
    if not isinstance(numeric, int):
        numeric = _logging.INFO
    root = _logging.getLogger()
    root.setLevel(numeric)
    for h in root.handlers[:]:
        root.removeHandler(h)

    class _Fmt(_logging.Formatter):
        def format(self, record: _logging.LogRecord) -> str:
            obj = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            for k, v in record.__dict__.items():
                if k in {"args", "msg", "message", "name", "levelname", "levelno",
                          "created", "filename", "funcName", "lineno", "module", "msecs",
                          "pathname", "process", "processName", "relativeCreated",
                          "stack_info", "exc_info", "exc_text", "thread", "threadName", "taskName"}:
                    continue
                if v is None:
                    continue
                obj[k] = v
            if record.exc_info:
                obj["exception"] = self.formatException(record.exc_info)
            return json.dumps(obj, default=str)

    handler = _logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Fmt())
    root.addHandler(handler)


def _sentry_init() -> None:
    s = get_settings()
    if not s.sentry_dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=s.sentry_dsn,
            environment=s.environment,
            release=s.release,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
            ],
            send_default_pii=False,
            attach_stacktrace=True,
        )
        sentry_sdk.set_tag("service", s.service_name)
    except Exception as exc:
        logging.getLogger(__name__).warning("Sentry init failed: %s", exc)


_setup_logging()
_sentry_init()

settings = get_settings()
app = FastAPI(title="Revenue Edge Webhooks", version="0.1.0")
app.include_router(retell_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/")
async def root() -> dict:
    return {"service": settings.service_name, "version": app.version}
