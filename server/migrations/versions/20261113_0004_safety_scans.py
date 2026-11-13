"""safety_scans table

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13 18:26:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "safety_scans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("target_prompt", sa.Text(), nullable=True),
        sa.Column("target_model", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("global_score", sa.Float(), nullable=False),
        sa.Column("total_probes", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("categories_summary", sa.JSON(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_safety_scans_project_id"), "safety_scans", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_safety_scans_project_id"), table_name="safety_scans")
    op.drop_table("safety_scans")
