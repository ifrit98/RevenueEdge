"""Revenue Edge API Gateway — FastAPI entrypoint.

Phase 0 scope: health/ready probes, internal queue enqueue, CORS, trace-ID,
structured logging, Sentry. Phase 1+ will add auth-protected CRUD for
businesses, channels, conversations, leads, tasks, and knowledge.
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
from .routes import health as health_routes
from .routes import queue as queue_routes
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
