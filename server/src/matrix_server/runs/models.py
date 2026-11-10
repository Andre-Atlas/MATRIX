"""Relational tables of training runs (contract G1): `runs`, `run_tags`, `run_artifacts`, `run_reports`.

Metric points live in the metric store (`runs.store`), not here. `runs.point_count` and
`runs.key_counts` count accepted point writes (a re-sent point counts again), which bounds the
per-run and per-key caps without querying the metric store on every ingest.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from matrix_server.storage.db import Base, new_id, utcnow

RUN_STATUSES = ("running", "finished", "failed", "killed")
CLOSED_STATUSES = frozenset({"finished", "failed", "killed"})
ARTIFACT_KINDS = ("file", "model", "plot", "table", "report")
REPORT_KINDS = ("classification", "regression", "importance", "table", "custom")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_project_started", "project_id", "started_at", "id"),
        Index("ix_runs_project_name", "project_id", "name", "id"),
        Index("ix_runs_project_status", "project_id", "status"),
        Index("ix_runs_project_group", "project_id", "group_name"),
        Index("ix_runs_project_sweep", "project_id", "sweep_id"),
        Index("ix_runs_status_heartbeat", "status", "heartbeat_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # `group` is an SQL keyword: the column is `group_name`, the attribute and API field `group`.
    group: Mapped[str | None] = mapped_column("group_name", String(200), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    git: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    env: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sweep_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    point_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    key_counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)


class RunTag(Base):
    """One row per (run, tag): exact `tag=` filters and `q` search without JSON operators."""

    __tablename__ = "run_tags"
    __table_args__ = (
        UniqueConstraint("run_id", "tag", name="uq_run_tags_run_tag"),
        Index("ix_run_tags_project_tag", "project_id", "tag"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tag: Mapped[str] = mapped_column(String(100), nullable=False)


class RunArtifact(Base):
    """Link run -> artifact (G0 `artifacts` table). `artifact_id` is a plain reference: ownership is
    checked by `service.artifact_exists`, and a link whose artifact is gone is not listed."""

    __tablename__ = "run_artifacts"
    __table_args__ = (UniqueConstraint("run_id", "artifact_id", name="uq_run_artifacts_run_artifact"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RunReport(Base):
    """Typed evaluation report; `data` was validated by `runs.reports` for its `kind`."""

    __tablename__ = "run_reports"
    __table_args__ = (Index("ix_run_reports_run_kind", "run_id", "kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    data: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
