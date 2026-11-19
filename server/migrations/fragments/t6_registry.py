"""T6 migration fragment (registry stage history, webhooks, unique production, lineage indexes).

Not an Alembic revision itself (HANDOFF §Migrações): the coordinator assembles 0009+.
Requires revision 0008.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

LINEAGE_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_runs_project_dataset_version", "runs", ["project_id", "dataset_version_id"]),
    ("ix_runs_project_parent_run", "runs", ["project_id", "parent_run_id"]),
    ("ix_mlops_mv_project_eval_exp", "mlops_model_versions", ["project_id", "eval_experiment_id"]),
    ("ix_mlops_mv_project_safety_scan", "mlops_model_versions", ["project_id", "safety_scan_id"]),
    ("ix_benchmark_runs_project_ds_commit", "benchmark_runs", ["project_id", "dataset_commit_id"]),
)


def _archive_duplicate_production_versions(bind: sa.Connection) -> None:
    """Ensure at most one `production` version exists per model before adding the partial unique index."""
    rows = bind.execute(
        sa.text(
            "SELECT id, model_id, version FROM mlops_model_versions "
            "WHERE stage = 'production' ORDER BY model_id, version DESC"
        )
    ).fetchall()
    seen: set[str] = set()
    to_archive: list[str] = []
    for row_id, model_id, _version in rows:
        if model_id in seen:
            to_archive.append(row_id)
        else:
            seen.add(model_id)
    for row_id in to_archive:
        bind.execute(
            sa.text("UPDATE mlops_model_versions SET stage = 'archived' WHERE id = :id"),
            {"id": row_id},
        )


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "model_stage_history",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column(
            "model_id",
            sa.String(length=32),
            sa.ForeignKey("mlops_registered_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("from_stage", sa.String(length=16), nullable=False),
        sa.Column("to_stage", sa.String(length=16), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_model_stage_history_project_id", "model_stage_history", ["project_id"])
    op.create_index("ix_model_stage_history_model_id", "model_stage_history", ["model_id"])
    op.create_index(
        "ix_model_stage_history_model_ts",
        "model_stage_history",
        ["project_id", "model_id", "ts"],
    )

    op.create_table(
        "model_webhooks",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column(
            "model_id",
            sa.String(length=32),
            sa.ForeignKey("mlops_registered_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_model_webhooks_project_id", "model_webhooks", ["project_id"])
    op.create_index("ix_model_webhooks_model_id", "model_webhooks", ["model_id"])
    op.create_index(
        "ix_model_webhooks_project_model",
        "model_webhooks",
        ["project_id", "model_id"],
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column(
            "webhook_id",
            sa.String(length=32),
            sa.ForeignKey("model_webhooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.String(length=32),
            sa.ForeignKey("mlops_registered_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_webhook_deliveries_project_id", "webhook_deliveries", ["project_id"])
    op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])
    op.create_index("ix_webhook_deliveries_model_id", "webhook_deliveries", ["model_id"])
    op.create_index(
        "ix_webhook_deliveries_webhook_created",
        "webhook_deliveries",
        ["project_id", "webhook_id", "created_at"],
    )

    _archive_duplicate_production_versions(bind)
    op.create_index(
        "uq_mlops_model_versions_one_production",
        "mlops_model_versions",
        ["model_id"],
        unique=True,
        sqlite_where=sa.text("stage = 'production'"),
        postgresql_where=sa.text("stage = 'production'"),
    )

    for idx_name, table_name, cols in LINEAGE_INDEXES:
        op.create_index(idx_name, table_name, cols)


def downgrade() -> None:
    for idx_name, table_name, _cols in reversed(LINEAGE_INDEXES):
        op.drop_index(idx_name, table_name=table_name)

    op.drop_index("uq_mlops_model_versions_one_production", table_name="mlops_model_versions")

    op.drop_index("ix_webhook_deliveries_webhook_created", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_model_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_webhook_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_project_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")

    op.drop_index("ix_model_webhooks_project_model", table_name="model_webhooks")
    op.drop_index("ix_model_webhooks_model_id", table_name="model_webhooks")
    op.drop_index("ix_model_webhooks_project_id", table_name="model_webhooks")
    op.drop_table("model_webhooks")

    op.drop_index("ix_model_stage_history_model_ts", table_name="model_stage_history")
    op.drop_index("ix_model_stage_history_model_id", table_name="model_stage_history")
    op.drop_index("ix_model_stage_history_project_id", table_name="model_stage_history")
    op.drop_table("model_stage_history")
