from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATRIX_")

    database_url: str = "sqlite:///./matrix.db"
    span_store: Literal["memory", "clickhouse"] = "memory"
    clickhouse_url: str = "http://localhost:8123"
    clickhouse_database: str = "matrix"
    admin_token: str | None = None
    max_body_bytes: int = 10_000_000
    max_attribute_chars: int = 100_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
