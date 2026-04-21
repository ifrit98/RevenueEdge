"""Minimal error hierarchy. Ported-down from SMB-MetaPattern's 579-line module.

Expand in later phases if needed (retry classification, typed error codes, etc.).
"""

from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    """Base class for application errors with an HTTP status code."""

    status_code: int = 500
    code: str = "app_error"

    def __init__(self, message: str, *, code: Optional[str] = None, context: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.context: dict[str, Any] = context or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    status_code = 422
    code = "validation"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"


class UpstreamError(AppError):
    """A downstream provider (Retell, Twilio, SendGrid, OpenAI) failed."""

    status_code = 502
    code = "upstream_error"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
