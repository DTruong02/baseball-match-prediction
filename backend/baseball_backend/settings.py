from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://baseball:baseball@localhost:5432/baseball"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Comma-separated browser origins allowed by CORS (production web origin, etc.).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    artifacts_root: Path = Path("artifacts")

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
    live_poll_interval_seconds: float = 15.0
    live_poll_game_delay_seconds: float = 0.5
    live_poll_min_request_interval_seconds: float = 0.5
    live_sync_retries: int = 2
    live_sync_backoff_seconds: float = 0.5
    live_stale_after_seconds: float = 90.0
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True
    redis_connect_timeout_seconds: float = 2.0
    live_cache_ttl_completed_seconds: int = 3600
    live_pubsub_enabled: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    notification_poll_interval_seconds: float = 30.0
    notification_email_retries: int = 2
    notification_email_backoff_seconds: float = 0.5
    # LLM / grounded AI (Stage 6.5). Also accepts OPENAI_* via chat_repl env fallback.
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    # Observability (Stage 7.4)
    log_level: str = "INFO"
    log_json: bool = True
    metrics_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
