"""Sentry initialization and context helpers.

Ported from SMB-MetaPattern with `tenant_id` renamed to `business_id`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Optional

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

logger = logging.getLogger(__name__)

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
RELEASE = os.getenv("RELEASE", "local")
SAMPLE_RATE = float(os.getenv("SENTRY_SAMPLE_RATE", "1.0"))
TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
SERVICE_NAME = os.getenv("SERVICE_NAME", "re-api")


def before_send(
    event: Dict[str, Any], hint: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if ENVIRONMENT == "development" and not os.getenv("SENTRY_DEV_ENABLED"):
        return None

    if "request" in event and "headers" in event["request"]:
        headers = event["request"]["headers"]
        for key in ("authorization", "x-api-key", "x-internal-key", "cookie"):
            if key in headers:
                headers[key] = "[Filtered]"

    if "exception" in event and "values" in event["exception"]:
        for exc in event["exception"]["values"]:
            if exc.get("value"):
                value = exc["value"]
                if "token" in value.lower() or "key" in value.lower():
                    exc["value"] = "[Potentially sensitive data filtered]"

    return event


def traces_sampler(sampling_context: Dict[str, Any]) -> float:
    transaction_name = sampling_context.get("transaction_context", {}).get("name", "")
    if "/health" in transaction_name:
        return 0.0
    if "/webhooks/" in transaction_name:
        return min(0.5, TRACES_SAMPLE_RATE * 5)
    return TRACES_SAMPLE_RATE


def init_sentry() -> bool:
    if not SENTRY_DSN:
        logger.info("Sentry DSN not configured, error monitoring disabled")
        return False
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=ENVIRONMENT,
        release=RELEASE,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
        ],
        sample_rate=SAMPLE_RATE,
        traces_sampler=traces_sampler,
        before_send=before_send,
        attach_stacktrace=True,
        send_default_pii=False,
        max_breadcrumbs=50,
        enable_tracing=True,
        profiles_sample_rate=0.1 if ENVIRONMENT == "production" else 0.0,
    )
    sentry_sdk.set_tag("service", SERVICE_NAME)
    logger.info("Sentry initialized for environment=%s service=%s", ENVIRONMENT, SERVICE_NAME)
    return True


def set_business_context(business_id: str, business_name: Optional[str] = None) -> None:
    sentry_sdk.set_tag("business_id", business_id)
    if business_name:
        sentry_sdk.set_tag("business_name", business_name)


def set_user_context(user_id: str, email: Optional[str] = None) -> None:
    sentry_sdk.set_user({"id": user_id, "email": email})


def set_trace_context(trace_id: str) -> None:
    sentry_sdk.set_tag("trace_id", trace_id)


def capture_message(message: str, level: str = "info", **extra: Any) -> None:
    with sentry_sdk.push_scope() as scope:
        for key, value in extra.items():
            scope.set_extra(key, value)
        sentry_sdk.capture_message(message, level=level)


def capture_exception(exception: Exception, **extra: Any) -> None:
    with sentry_sdk.push_scope() as scope:
        for key, value in extra.items():
            scope.set_extra(key, value)
        sentry_sdk.capture_exception(exception)
