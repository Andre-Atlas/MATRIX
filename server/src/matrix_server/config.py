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

    # Training runs (contract G1): caps on accepted metric point writes per run and per key.
    max_points_per_run: int = 50_000_000
    max_points_per_key: int = 10_000_000

    # Users and web sessions (ADR-006, contract G0 section 1).
    session_idle_ttl_seconds: int = 7 * 24 * 3600
    session_absolute_ttl_seconds: int = 30 * 24 * 3600
    # Whether any logged-in user may create projects (POST /api/v1/projects).
    allow_project_creation: bool = True

    # Artifact store (ADR-007, contract G0 section 2). Raw upload routes are exempt from the JSON
    # body limit and bounded by max_artifact_bytes instead (limits.py).
    artifact_backend: Literal["local", "s3"] = "local"
    artifact_local_dir: str = "./artifacts"
    s3_endpoint: str | None = None
    s3_public_endpoint: str | None = None
    s3_bucket: str = "matrix-artifacts"
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_auto_create_bucket: bool = False
    max_artifact_bytes: int = 5 * 1024**3
    project_quota_bytes: int = 50 * 1024**3
    verify_hash_max_bytes: int = 256 * 1024**2
    artifact_url_ttl_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
