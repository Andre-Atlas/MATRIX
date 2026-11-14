"""Relational models (PostgreSQL in production, SQLite in tests/dev)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from matrix_server.storage.db import Base, new_id, utcnow

# --- Projects and API keys -------------------------------------------------


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="project", cascade="all, delete-orphan")
    members: Mapped[list[ProjectMember]] = relationship(back_populates="project", cascade="all, delete-orphan")


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

    project: Mapped[Project] = relationship(back_populates="api_keys")


# --- Users and Memberships -------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    memberships: Mapped[list[ProjectMember]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        Index("ix_project_members_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")  # admin, member, viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


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
    branches: Mapped[list[DatasetBranch]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )
    commits: Mapped[list[DatasetCommit]] = relationship(
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
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
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


# --- Defensive Security & OWASP Top 10 Scans --------------------------------


class SafetyScan(Base):
    """Defensive safety audit and compliance scan report (OWASP Top 10 for LLMs)."""

    __tablename__ = "safety_scans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="passed")
    global_score: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    total_probes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    categories_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- MLOps Studio (Training, Sweeps, Artifacts, Lineage & Model Registry) ----


class MLOpsSweep(Base):
    """Hyperparameter sweep configuration and status.

    `best_run_id` intentionally has no FK to `mlops_training_runs` to avoid a circular FK
    with `TrainingRun.sweep_id` and keep Alembic downgrades cleanly reversible.
    """

    __tablename__ = "mlops_sweeps"
    __table_args__ = (Index("ix_mlops_sweeps_project_status", "project_id", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="random")
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    metric_goal: Mapped[str] = mapped_column(String(16), nullable=False, default="minimize")
    search_space: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    early_stopping: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    max_trials: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    best_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    best_metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrainingRun(Base):
    """Training / fine-tuning / classic ML run metadata."""

    __tablename__ = "mlops_training_runs"
    __table_args__ = (
        Index("ix_mlops_training_runs_project_status", "project_id", "status"),
        Index("ix_mlops_training_runs_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    run_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sft")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    base_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sweep_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("mlops_sweeps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("mlops_training_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    hyperparams: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    summary_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    hardware_info: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    classic_eval: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    total_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    """Versioned binary artifact (checkpoint, LoRA adapter, dataset, model) backed by S3/LocalFS/external."""

    __tablename__ = "mlops_artifacts"
    __table_args__ = (
        UniqueConstraint("project_id", "name", "version", name="uq_mlops_artifacts_name_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("mlops_training_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="checkpoint")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    storage_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_upload")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArtifactLineage(Base):
    """Directed acyclic graph edge connecting datasets, runs, artifacts, model versions, and evals."""

    __tablename__ = "mlops_artifact_lineage"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "relation",
            name="uq_mlops_lineage_edge",
        ),
        Index("ix_mlops_lineage_source", "project_id", "source_type", "source_id"),
        Index("ix_mlops_lineage_target", "project_id", "target_type", "target_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RegisteredModel(Base):
    """Logical model entry in the Model & LoRA Registry."""

    __tablename__ = "mlops_registered_models"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_mlops_registered_models_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False, default="llm")
    base_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    versions: Mapped[list[ModelVersion]] = relationship(
        back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )
    aliases: Mapped[list[ModelAlias]] = relationship(
        back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )


class ModelVersion(Base):
    """Immutable registered version of a model or LoRA adapter."""

    __tablename__ = "mlops_model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_mlops_model_versions_model_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("mlops_registered_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("mlops_artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("mlops_training_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    eval_experiment_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True
    )
    safety_scan_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("safety_scans.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    lora_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    signature: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metrics_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model_card: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    model: Mapped[RegisteredModel] = relationship(back_populates="versions")


class ModelAlias(Base):
    """Mutable pointer `alias -> version` for a registered model (e.g. champion, challenger)."""

    __tablename__ = "mlops_model_aliases"
    __table_args__ = (UniqueConstraint("model_id", "alias", name="uq_mlops_model_aliases_model_alias"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("mlops_registered_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    model: Mapped[RegisteredModel] = relationship(back_populates="aliases")


# --- Zero-Copy Data Versioning ---------------------------------------------


class DatasetBranch(Base):
    """Zero-copy mutable branch pointer `(dataset_id, name) -> head_commit_id`."""

    __tablename__ = "dataset_branches"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="uq_dataset_branches_dataset_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    head_commit_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    dataset: Mapped[Dataset] = relationship(back_populates="branches")


class DatasetCommit(Base):
    """Immutable snapshot commit of a dataset."""

    __tablename__ = "dataset_commits"
    __table_args__ = (
        Index("ix_dataset_commits_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_commit_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    second_parent_commit_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    dataset: Mapped[Dataset] = relationship(back_populates="commits")
    items: Mapped[list[DatasetCommitItem]] = relationship(
        back_populates="commit", cascade="all, delete-orphan", passive_deletes=True
    )


class DatasetCommitItem(Base):
    """Immutable versioned item within a dataset commit."""

    __tablename__ = "dataset_commit_items"
    __table_args__ = (
        UniqueConstraint("commit_id", "item_key", name="uq_dataset_commit_items_commit_item_key"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    commit_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("dataset_commits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    artifact_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    commit: Mapped[DatasetCommit] = relationship(back_populates="items")


# --- Benchmarks (LM-Eval & VLMEvalKit) -------------------------------------


class BenchmarkRun(Base):
    """Foundational and multimodal benchmark run (LM-Eval / VLMEvalKit)."""

    __tablename__ = "benchmark_runs"
    __table_args__ = (
        Index("ix_benchmark_runs_project_created", "project_id", "created_at"),
        Index("ix_benchmark_runs_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    suite: Mapped[str] = mapped_column(String(32), nullable=False)
    tasks: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    runner_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="local_sim")
    runner_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    eval_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="generate_until")
    num_fewshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_model: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("mlops_model_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_commit_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    summary_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task_results: Mapped[list[BenchmarkTaskResult]] = relationship(
        back_populates="benchmark_run", cascade="all, delete-orphan", passive_deletes=True
    )


class BenchmarkTaskResult(Base):
    """Task-level result within a benchmark run."""

    __tablename__ = "benchmark_task_results"
    __table_args__ = (
        UniqueConstraint("benchmark_run_id", "task_name", name="uq_benchmark_task_results_run_task"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    benchmark_run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_name: Mapped[str] = mapped_column(String(200), nullable=False)
    modality: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    eval_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="generate_until")
    num_fewshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    primary_metric: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms_p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    benchmark_run: Mapped[BenchmarkRun] = relationship(back_populates="task_results")


# --- Inspect AI Sandboxes --------------------------------------------------


class SandboxRun(Base):
    """Inspect AI agent sandbox evaluation run."""

    __tablename__ = "sandbox_runs"
    __table_args__ = (
        Index("ix_sandbox_runs_project_created", "project_id", "created_at"),
        Index("ix_sandbox_runs_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    runner_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="local_sandbox")
    dataset_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_commit_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    solver_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scorer_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sandbox_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transcripts: Mapped[list[SandboxTaskTranscript]] = relationship(
        back_populates="sandbox_run", cascade="all, delete-orphan", passive_deletes=True
    )


class SandboxTaskTranscript(Base):
    """Step-by-step transcript and score verification for an Inspect AI task."""

    __tablename__ = "sandbox_task_transcripts"
    __table_args__ = (
        UniqueConstraint("sandbox_run_id", "task_key", name="uq_sandbox_task_transcripts_run_task"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sandbox_run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sandbox_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="passed")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    scorer_details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    verified_artifacts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    sandbox_run: Mapped[SandboxRun] = relationship(back_populates="transcripts")


