"""Relational tables for AI Gateway shared state (T8): rate windows and request audit logs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from matrix_server.storage.db import Base, new_id, utcnow


class GatewayRateWindow(Base):
    """Fixed 60-second rate-limit counter window shared across gateway workers."""

    __tablename__ = "gateway_rate_windows"
    __table_args__ = (
        Index("ix_gateway_rate_windows_window_start", "window_start"),
    )

    project_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    window_start: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class GatewayRequest(Base):
    """Persistent per-project audit log and telemetry source for gateway requests."""

    __tablename__ = "gateway_requests"
    __table_args__ = (
        Index("ix_gateway_requests_project_ts", "project_id", "ts"),
        Index("ix_gateway_requests_ts", "ts"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    route: Mapped[str] = mapped_column(String(128), nullable=False, default="/api/v1/gateway/chat/completions")
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_sanitized: Mapped[str | None] = mapped_column(String(500), nullable=True)
