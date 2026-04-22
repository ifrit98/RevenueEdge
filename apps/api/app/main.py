"""Revenue Edge API Gateway — FastAPI entrypoint.

Scope:
  - Health / ready probes.
  - Internal queue enqueue (webhook → worker handshake).
  - CRUD for businesses, conversations, tasks (JWT-protected; header
    fallback gated on `X-Internal-Key`).
  - Metric snapshots + rollup trigger.
  - In-process scheduler that runs `run_daily_rollup` on an interval.

Observability: trace-ID middleware, structured JSON logging, Sentry.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings, validate_startup_settings
from .errors import AppError
from .logging_config import setup_logging
from .monitoring import capture_exception, init_sentry
from .routes import (
    businesses as businesses_routes,
    channels as channels_routes,
    conversations as conversations_routes,
    health as health_routes,
    knowledge as knowledge_routes,
    leads as leads_routes,
    metrics as metrics_routes,
    queue as queue_routes,
    tasks as tasks_routes,
)
from .services.scheduler import start_scheduler, stop_scheduler
from .trace import TraceMiddleware, current_trace_id

setup_logging()
logger = logging.getLogger(__name__)

settings = get_settings()
try:
    validate_startup_settings(settings)
except RuntimeError as exc:
    logger.error("Startup validation failed: %s", exc)
    if settings.environment.lower() not in {"development", "test"}:
        raise

init_sentry()

app = FastAPI(
    title="Revenue Edge API",
    version="0.1.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(TraceMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-ID"],
)

app.include_router(health_routes.router)
app.include_router(queue_routes.router)
app.include_router(businesses_routes.router)
app.include_router(channels_routes.router)
app.include_router(conversations_routes.router)
app.include_router(knowledge_routes.router)
app.include_router(leads_routes.router)
app.include_router(tasks_routes.router)
app.include_router(metrics_routes.router)


@app.on_event("startup")
async def _on_startup() -> None:
    start_scheduler()


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    await stop_scheduler()


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "%s: %s",
        exc.code,
        exc.message,
        extra={"trace_id": current_trace_id(), "path": request.url.path, "context": exc.context},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "trace_id": current_trace_id(),
            }
        },
    )


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "http_error",
                "message": exc.detail,
                "trace_id": current_trace_id(),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception at %s", request.url.path)
    capture_exception(exc, path=request.url.path, trace_id=current_trace_id())
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Internal server error",
                "trace_id": current_trace_id(),
            }
        },
    )


@app.get("/")
async def root() -> dict:
    return {"service": settings.service_name, "version": app.version}
