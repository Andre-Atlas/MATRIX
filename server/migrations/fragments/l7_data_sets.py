"""L7 migration fragment: file data-sets (`data_sets`, `data_set_versions`, `data_set_files`,
`data_set_tags`; contract G3 section 2).

Not an Alembic revision itself (the coordinator assembles 0010+). Requires revision 0009.
Purely additive: `downgrade()` drops the four tables (and the manifests and tags in them; the
artifacts they point at are untouched).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

TABLES = ("data_set_tags", "data_set_files", "data_set_versions", "data_sets")  # drop order


def upgrade() -> None:
    op.create_table(
        "data_sets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_data_sets_project_name"),
    )
    op.create_index("ix_data_sets_project_id", "data_sets", ["project_id"])

    op.create_table(
        "data_set_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("data_set_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["data_set_id"], ["data_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("data_set_id", "version", name="uq_data_set_versions_number"),
    )
    op.create_index("ix_data_set_versions_project", "data_set_versions", ["project_id"])

    op.create_table(
        "data_set_files",
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("artifact_id", sa.String(length=32), nullable=True),
        sa.Column("media_type", sa.String(length=127), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["data_set_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("version_id", "path", name="pk_data_set_files"),
    )
    op.create_index("ix_data_set_files_project_sha", "data_set_files", ["project_id", "sha256"])
    op.create_index("ix_data_set_files_artifact", "data_set_files", ["artifact_id"])

    op.create_table(
        "data_set_tags",
        sa.Column("data_set_id", sa.String(length=32), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["data_set_id"], ["data_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("data_set_id", "tag", name="pk_data_set_tags"),
    )
    op.create_index("ix_data_set_tags_project", "data_set_tags", ["project_id"])


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)  # indexes go with the table
