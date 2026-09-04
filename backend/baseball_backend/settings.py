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
    artifacts_root: Path = Path("artifacts")
    live_poll_interval_seconds: float = 15.0
    live_poll_game_delay_seconds: float = 0.5
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True
    redis_connect_timeout_seconds: float = 2.0
    live_cache_ttl_completed_seconds: int = 3600
    live_pubsub_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
