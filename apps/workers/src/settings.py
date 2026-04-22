"""Worker configuration. A narrower slice of apps/api/app/config.py — workers
only need Supabase credentials, an internal-service key for gateway callbacks,
and a few tunables.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
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
    retell_from_number: str = ""
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: str = ""
    twilio_messaging_service_sid: Optional[str] = None
    sendgrid_api_key: Optional[str] = None
    default_email_from: str = "no-reply@revenueedge.local"

    openai_api_key: str = ""
    llm_chat_model: str = "gpt-4.1-mini"
    llm_embedding_model: str = "text-embedding-3-small"

    environment: str = "development"
    release: str = "local"
    log_level: str = "INFO"
    service_name: str = "re-workers"
    sentry_dsn: str = ""

    worker_poll_interval_seconds: float = 2.0
    worker_claim_batch_size: int = 5
    worker_lock_timeout_seconds: int = 300

    # Comma-separated list of worker names to enable in this process.
    workers: str = "inbound_normalizer,conversation_intelligence,outbound_action,handoff,followup_scheduler,knowledge_ingestion"

    @property
    def enabled_workers(self) -> List[str]:
        return [w.strip() for w in self.workers.split(",") if w.strip()]


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
