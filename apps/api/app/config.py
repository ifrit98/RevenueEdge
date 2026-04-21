"""Centralized configuration for Revenue Edge services.

Pydantic-settings backed. `get_settings()` is a cached singleton. All three
services (api, webhooks, workers) import from this module to keep a single
source of truth for env vars.

Ported from SMB-MetaPattern/apps/api-gateway/app/config.py with the
real-estate specific knobs (FUB, HubSpot, model-router, agent IDs) removed.
See docs/DEFERRED_MODEL_ROUTER.md for how to add model-router back when
multi-provider LLM routing becomes worthwhile.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Supabase ----------------------------------------------------------
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE"),
    )
    supabase_jwt_secret: str = ""

    # ---- Retell ------------------------------------------------------------
    retell_api_key: str = ""
    retell_webhook_secret: str = ""
    retell_from_number: str = ""

    # ---- Twilio (fallback / compliance only) -------------------------------
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: str = ""
    twilio_messaging_service_sid: Optional[str] = None

    # ---- Email -------------------------------------------------------------
    sendgrid_api_key: Optional[str] = None
    default_email_from: str = "no-reply@revenueedge.local"

    # ---- LLM (single-provider for MVP) -------------------------------------
    openai_api_key: str = ""
    llm_chat_model: str = "gpt-4.1-mini"
    llm_embedding_model: str = "text-embedding-3-small"

    # ---- Internal service auth --------------------------------------------
    internal_service_key: str = ""
    internal_tool_user_id: str = ""

    # ---- CORS --------------------------------------------------------------
    cors_allowed_origins: str = "http://localhost:3000"

    # ---- Environment / App ------------------------------------------------
    environment: str = "development"
    release: str = "local"
    log_level: str = "INFO"
    strict_startup_validation: bool = True
    service_name: str = "re-api"

    # ---- Sentry ------------------------------------------------------------
    sentry_dsn: str = ""
    sentry_sample_rate: float = 1.0
    sentry_traces_sample_rate: float = 0.1
    sentry_dev_enabled: bool = False

    # ---- Workers -----------------------------------------------------------
    worker_poll_interval_seconds: float = 2.0
    worker_claim_batch_size: int = 5
    worker_lock_timeout_seconds: int = 300

    # ---- Service URLs ------------------------------------------------------
    re_api_url: str = "http://localhost:8080"
    re_webhooks_url: str = "http://localhost:8081"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def required_settings_for_startup(settings: Settings) -> Dict[str, str]:
    if settings.environment.lower() == "test":
        return {}
    return {
        "supabase_url": settings.supabase_url,
        "supabase_service_key": settings.supabase_service_key,
        "supabase_jwt_secret": settings.supabase_jwt_secret,
    }


def validate_startup_settings(settings: Optional[Settings] = None) -> None:
    s = settings or get_settings()
    if not s.strict_startup_validation:
        return
    required = required_settings_for_startup(s)
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required startup settings: {', '.join(missing)}")
