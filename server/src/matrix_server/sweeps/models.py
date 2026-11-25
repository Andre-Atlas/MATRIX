"""Relational tables for hyperparameter sweeps (contract G2): `sweeps` and `sweep_runs`.

`sweeps.best_run_id` is a plain reference to `runs.id` (no FK, like `runs.sweep_id`), avoiding a
circular foreign key between `sweeps` and `runs`. Deleting a sweep clears `runs.sweep_id` while
keeping the runs, and cascades `sweep_runs`.
"""

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
from sqlalchemy.orm import Mapped, mapped_column

from matrix_server.storage.db import Base, new_id, utcnow

SWEEP_STATUSES = ("running", "paused", "finished", "stopped")


class Sweep(Base):
    __tablename__ = "sweeps"
    __table_args__ = (
        Index("ix_sweeps_project_status", "project_id", "status"),
        Index("ix_sweeps_project_created", "project_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metric_key: Mapped[str] = mapped_column(String(200), nullable=False)
    metric_goal: Mapped[str] = mapped_column(String(16), nullable=False, default="minimize")
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    best_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    importance_cache: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SweepRun(Base):
    """Per-trial state linking a `Sweep` to its canonical `Run`.

    `UniqueConstraint("sweep_id", "trial_index")` guarantees that concurrent `POST /sweeps/{id}/next`
    calls never receive the same trial index (and thus never the same grid/random configuration).
    """

    __tablename__ = "sweep_runs"
    __table_args__ = (
        UniqueConstraint("sweep_id", "trial_index", name="uq_sweep_runs_sweep_index"),
        UniqueConstraint("sweep_id", "run_id", name="uq_sweep_runs_sweep_run"),
        Index("ix_sweep_runs_sweep_bracket", "sweep_id", "bracket"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sweep_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sweeps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trial_index: Mapped[int] = mapped_column(Integer, nullable=False)
    bracket: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pruned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
