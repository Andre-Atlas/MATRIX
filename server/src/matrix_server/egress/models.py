"""`egress_hosts`: per-project allowlist for outbound requests (ADR-012, contract G0 section 1)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from matrix_server.storage.db import Base, new_id, utcnow


class EgressHost(Base):
    __tablename__ = "egress_hosts"
    __table_args__ = (UniqueConstraint("project_id", "host", name="uq_egress_hosts_project_host"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Normalized (lowercase ASCII/IDNA, no trailing dot): `example.com` or `*.example.com`.
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
