"""Webhook service configuration. Subset of apps/api/app/config.py."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebhookSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = ""
    supabase_service_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE"),
    )

    re_api_url: str = "http://re-api:8080"
    internal_service_key: str = ""

    retell_api_key: str = ""
    retell_webhook_secret: str = ""

    environment: str = "development"
    release: str = "local"
    log_level: str = "INFO"
    service_name: str = "re-webhooks"
    sentry_dsn: str = ""

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)


@lru_cache(maxsize=1)
def get_settings() -> WebhookSettings:
    return WebhookSettings()
