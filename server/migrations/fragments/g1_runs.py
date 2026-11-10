"""G1 (training runs) part of the cycle-2 revision 0003 — not a revision itself.

Alembic only loads `migrations/versions/`; the 0003 revision (generated once every cycle-2 model
has landed) calls `upgrade()` / `downgrade()` from here. Matches `matrix_server.runs.models`
(checked by `tests/test_runs_migration.py`). Requires `projects` (0001).
"""

import sqlalchemy as sa
from alembic import op

TABLES = ("runs", "run_tags", "run_artifacts", "run_reports")


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("group_name", sa.String(length=200), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("git", sa.JSON(), nullable=True),
        sa.Column("env", sa.JSON(), nullable=True),
        sa.Column("dataset_version_id", sa.String(length=64), nullable=True),
        sa.Column("sweep_id", sa.String(length=64), nullable=True),
        sa.Column("parent_run_id", sa.String(length=32), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("point_count", sa.BigInteger(), nullable=False),
        sa.Column("key_counts", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_project_started", "runs", ["project_id", "started_at", "id"])
    op.create_index("ix_runs_project_name", "runs", ["project_id", "name", "id"])
    op.create_index("ix_runs_project_status", "runs", ["project_id", "status"])
    op.create_index("ix_runs_project_group", "runs", ["project_id", "group_name"])
    op.create_index("ix_runs_project_sweep", "runs", ["project_id", "sweep_id"])
    op.create_index("ix_runs_status_heartbeat", "runs", ["status", "heartbeat_at"])

    op.create_table(
        "run_tags",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("tag", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "tag", name="uq_run_tags_run_tag"),
    )
    op.create_index("ix_run_tags_project_tag", "run_tags", ["project_id", "tag"])

    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("artifact_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "artifact_id", name="uq_run_artifacts_run_artifact"),
    )
    op.create_index("ix_run_artifacts_run_id", "run_artifacts", ["run_id"])
    op.create_index("ix_run_artifacts_project_id", "run_artifacts", ["project_id"])

    op.create_table(
        "run_reports",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_reports_run_kind", "run_reports", ["run_id", "kind"])
    op.create_index("ix_run_reports_project_id", "run_reports", ["project_id"])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
