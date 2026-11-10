"""Relational models (PostgreSQL in production, SQLite in tests/dev)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from matrix_server.storage.db import Base, new_id, utcnow

# --- Projects and API keys -------------------------------------------------

API_KEY_SCOPES = ("ingest", "read", "write")
_ALL_SCOPES_SQL = text("'[\"ingest\", \"read\", \"write\"]'")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # G0 (ADR-006): scopes are a subset of API_KEY_SCOPES; keys from before G0 get all three.
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=lambda: list(API_KEY_SCOPES), server_default=_ALL_SCOPES_SQL
    )
    created_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    project: Mapped[Project] = relationship(back_populates="api_keys")


# --- Scores ----------------------------------------------------------------


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (
        Index("ix_scores_project_trace", "project_id", "trace_id"),
        # Automated scores carry a dedup key (online evals: "online:{eval}:{trace}:{span}") so a
        # job delivered twice records one score; NULL (human/SDK scores) never conflicts.
        UniqueConstraint("project_id", "dedup_key", name="uq_scores_dedup"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    span_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    dedup_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Prompts ---------------------------------------------------------------


class PromptVersion(Base):
    """Immutable version of a prompt; `template` is a string ("text") or a list of messages ("chat")."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("project_id", "name", "version", name="uq_prompt_versions_name_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    template: Mapped[Any] = mapped_column(JSON, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    response_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    commit_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PromptLabel(Base):
    """Movable pointer `label -> version` for a prompt name (rollback = move it back)."""

    __tablename__ = "prompt_labels"
    __table_args__ = (UniqueConstraint("project_id", "name", "label", name="uq_prompt_labels_name_label"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --- Datasets and experiments ----------------------------------------------


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_datasets_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    items: Mapped[list[DatasetItem]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )


class DatasetItem(Base):
    __tablename__ = "dataset_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # `metadata` is reserved on declarative classes; the column keeps the contract name.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    source_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_span_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    dataset: Mapped[Dataset] = relationship(back_populates="items")


class Experiment(Base):
    """Offline experiment; `variants`/`metrics` follow the POST /experiments contract, `summary` is
    `Record<variant, Record<metric, {mean, pass_rate}>>`. Status: queued|running|done|failed."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # No cascade: a dataset referenced by experiments cannot be deleted silently.
    dataset_id: Mapped[str] = mapped_column(String(32), ForeignKey("datasets.id"), nullable=False, index=True)
    baseline_experiment_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    metrics: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list[ExperimentResult]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan", passive_deletes=True
    )


class ExperimentResult(Base):
    """One (item, variant) run; `scores` is `Record<metric, {value, passed, reason}>`."""

    __tablename__ = "experiment_results"
    __table_args__ = (
        UniqueConstraint("experiment_id", "item_id", "variant", name="uq_experiment_results_item_variant"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Plain reference (no FK): results survive deletion of the dataset item they were computed on.
    item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    variant: Mapped[str] = mapped_column(String(100), nullable=False)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    experiment: Mapped[Experiment] = relationship(back_populates="results")


# --- Annotation, rules, online evaluation -----------------------------------


class Annotation(Base):
    """Human annotation queue entry. Status: pending|done.

    At most one *pending* annotation per (project, trace): `dedup_key` holds the trace id while
    pending and NULL once done (NULLs never collide in a unique constraint on SQLite or PostgreSQL).
    Use `storage.annotations.open_annotation` to create one.
    """

    __tablename__ = "annotations"
    __table_args__ = (
        UniqueConstraint("project_id", "dedup_key", name="uq_annotations_pending"),
        Index("ix_annotations_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    dedup_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scores: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    corrected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PreferencePair(Base):
    """DPO pair `{prompt, chosen, rejected}` produced when an annotation corrects an output."""

    __tablename__ = "preference_pairs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    annotation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("annotations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    chosen: Mapped[str] = mapped_column(Text, nullable=False)
    rejected: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Rule(Base):
    """`condition` and `actions` follow the POST /rules contract."""

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    condition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    actions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    dataset_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OnlineEval(Base):
    __tablename__ = "online_evals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sampling_rate: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    filter: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rule_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("rules.id", ondelete="SET NULL"), nullable=True
    )
    online_eval_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("online_evals.id", ondelete="SET NULL"), nullable=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Job queue ---------------------------------------------------------------


class Job(Base):
    """Background job (ADR-003). Status: queued|running|done|failed. See `storage.jobs`."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Audit log (G0) ---------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_project_ts", "project_id", "ts"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # user | api_key | system
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


# --- Users, memberships, web sessions (G0, ADR-006) ---------------------------


class User(Base):
    """Person who logs in to the web UI. `email` is stored lower-cased and unique."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Membership(Base):
    """Role of a user in a project: viewer | member | admin | owner (see `auth.Role`)."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_memberships_user_project"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WebSession(Base):
    """Opaque server-side session. `id` is the SHA-256 (hex) of the `mxs_` token; the token itself
    is never stored. Valid while not revoked, before `expires_at` (absolute) and while
    `last_seen_at` is within the idle TTL."""

    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class LoginAttempt(Base):
    """Failed-login throttle per (e-mail, IP). `key` is a SHA-256 of both, so the table holds no
    e-mail and unknown e-mails are throttled exactly like known ones. `lock_count` escalates the
    lock duration and resets on a successful login."""

    __tablename__ = "login_attempts"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# --- Model modules registered here (one import per module, appended by each task) ------------
# Keeps Base.metadata complete for Alembic and create_all without every task editing this file.
import matrix_server.runs.models  # noqa: E402, F401
import matrix_server.artifacts.models
import matrix_server.egress.models  # noqa: F401
