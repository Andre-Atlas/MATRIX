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
    # Every request body (OTLP ingest included) is bounded by max_body_bytes; the JSON API
    # (/api/v1/*) by the smaller max_json_body_bytes. Larger bodies get 413.
    max_body_bytes: int = 10_000_000
    max_json_body_bytes: int = 2_000_000
    max_attribute_chars: int = 100_000
    # Bound on the decoded attribute size of one request (resource/scope attributes are copied
    # into each span): max_normalized_factor x max_body_bytes, else 413.
    max_normalized_factor: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
