"""File data-sets (contract G3 section 2): `data_sets`, `data_set_versions`, `data_set_files`,
`data_set_tags`.

A version is an immutable manifest of files. Each file points at a blob of the project's artifact
store by `sha256` (and, as a convenience, at the `artifacts` row it was created from; that column is
a plain reference without a foreign key, since a version must stay readable by digest). The
`latest` alias is not stored: it is always the highest version number.

Every table carries `project_id` so that `projects.delete_project` (generic sweep) reaches them.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from matrix_server.storage.db import Base, new_id, utcnow


class DataSet(Base):
    __tablename__ = "data_sets"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_data_sets_project_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="files")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DataSetVersion(Base):
    __tablename__ = "data_set_versions"
    __table_args__ = (
        UniqueConstraint("data_set_id", "version", name="uq_data_set_versions_number"),
        Index("ix_data_set_versions_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    data_set_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_sets.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="upload")
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DataSetFile(Base):
    __tablename__ = "data_set_files"
    __table_args__ = (
        PrimaryKeyConstraint("version_id", "path", name="pk_data_set_files"),
        Index("ix_data_set_files_project_sha", "project_id", "sha256"),
        Index("ix_data_set_files_artifact", "artifact_id"),
    )

    version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_set_versions.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(127), nullable=True)


class DataSetTag(Base):
    __tablename__ = "data_set_tags"
    __table_args__ = (
        PrimaryKeyConstraint("data_set_id", "tag", name="pk_data_set_tags"),
        Index("ix_data_set_tags_project", "project_id"),
    )

    data_set_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_sets.id", ondelete="CASCADE"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
