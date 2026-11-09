"""score dedup key

`scores.dedup_key` (nullable) with a unique `(project_id, dedup_key)` constraint: automated scores
(online evals) record one row per (eval, trace, span) even when their job is delivered twice.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.add_column(sa.Column("dedup_key", sa.String(length=200), nullable=True))
        batch_op.create_unique_constraint("uq_scores_dedup", ["project_id", "dedup_key"])


def downgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_constraint("uq_scores_dedup", type_="unique")
        batch_op.drop_column("dedup_key")
