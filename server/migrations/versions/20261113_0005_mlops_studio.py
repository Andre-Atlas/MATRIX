"""mlops studio tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13 18:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mlops_sweeps",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("metric_name", sa.String(length=200), nullable=False),
        sa.Column("metric_goal", sa.String(length=16), nullable=False),
        sa.Column("search_space", sa.JSON(), nullable=False),
        sa.Column("early_stopping", sa.JSON(), nullable=True),
        sa.Column("max_trials", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("best_run_id", sa.String(length=32), nullable=True),
        sa.Column("best_metric_value", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mlops_sweeps_project_id"), "mlops_sweeps", ["project_id"], unique=False)
    op.create_index("ix_mlops_sweeps_project_status", "mlops_sweeps", ["project_id", "status"], unique=False)

    op.create_table(
        "mlops_training_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("base_model", sa.String(length=200), nullable=True),
        sa.Column("dataset_id", sa.String(length=32), nullable=True),
        sa.Column("sweep_id", sa.String(length=32), nullable=True),
        sa.Column("parent_run_id", sa.String(length=32), nullable=True),
        sa.Column("hyperparams", sa.JSON(), nullable=False),
        sa.Column("summary_metrics", sa.JSON(), nullable=False),
        sa.Column("hardware_info", sa.JSON(), nullable=True),
        sa.Column("classic_eval", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("total_steps", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["mlops_training_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sweep_id"], ["mlops_sweeps.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mlops_training_runs_dataset_id"), "mlops_training_runs", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_mlops_training_runs_parent_run_id"), "mlops_training_runs", ["parent_run_id"], unique=False)
    op.create_index("ix_mlops_training_runs_project_created", "mlops_training_runs", ["project_id", "created_at"], unique=False)
    op.create_index(op.f("ix_mlops_training_runs_project_id"), "mlops_training_runs", ["project_id"], unique=False)
    op.create_index("ix_mlops_training_runs_project_status", "mlops_training_runs", ["project_id", "status"], unique=False)
    op.create_index(op.f("ix_mlops_training_runs_sweep_id"), "mlops_training_runs", ["sweep_id"], unique=False)

    op.create_table(
        "mlops_artifacts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_backend", sa.String(length=16), nullable=False),
        sa.Column("storage_uri", sa.String(length=1000), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_type", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["mlops_training_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_mlops_artifacts_name_version"),
    )
    op.create_index(op.f("ix_mlops_artifacts_project_id"), "mlops_artifacts", ["project_id"], unique=False)
    op.create_index(op.f("ix_mlops_artifacts_run_id"), "mlops_artifacts", ["run_id"], unique=False)
    op.create_index(op.f("ix_mlops_artifacts_sha256"), "mlops_artifacts", ["sha256"], unique=False)

    op.create_table(
        "mlops_artifact_lineage",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "relation",
            name="uq_mlops_lineage_edge",
        ),
    )
    op.create_index(op.f("ix_mlops_artifact_lineage_project_id"), "mlops_artifact_lineage", ["project_id"], unique=False)
    op.create_index("ix_mlops_lineage_source", "mlops_artifact_lineage", ["project_id", "source_type", "source_id"], unique=False)
    op.create_index("ix_mlops_lineage_target", "mlops_artifact_lineage", ["project_id", "target_type", "target_id"], unique=False)

    op.create_table(
        "mlops_registered_models",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_type", sa.String(length=32), nullable=False),
        sa.Column("base_model", sa.String(length=200), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_mlops_registered_models_name"),
    )
    op.create_index(op.f("ix_mlops_registered_models_project_id"), "mlops_registered_models", ["project_id"], unique=False)

    op.create_table(
        "mlops_model_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.String(length=32), nullable=True),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("eval_experiment_id", sa.String(length=32), nullable=True),
        sa.Column("safety_scan_id", sa.String(length=32), nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("lora_config", sa.JSON(), nullable=True),
        sa.Column("signature", sa.JSON(), nullable=True),
        sa.Column("metrics_snapshot", sa.JSON(), nullable=False),
        sa.Column("model_card", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["mlops_artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["eval_experiment_id"], ["experiments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_id"], ["mlops_registered_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["mlops_training_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["safety_scan_id"], ["safety_scans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", "version", name="uq_mlops_model_versions_model_version"),
    )
    op.create_index(op.f("ix_mlops_model_versions_artifact_id"), "mlops_model_versions", ["artifact_id"], unique=False)
    op.create_index(op.f("ix_mlops_model_versions_model_id"), "mlops_model_versions", ["model_id"], unique=False)
    op.create_index(op.f("ix_mlops_model_versions_project_id"), "mlops_model_versions", ["project_id"], unique=False)
    op.create_index(op.f("ix_mlops_model_versions_run_id"), "mlops_model_versions", ["run_id"], unique=False)

    op.create_table(
        "mlops_model_aliases",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=32), nullable=False),
        sa.Column("alias", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["mlops_registered_models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", "alias", name="uq_mlops_model_aliases_model_alias"),
    )
    op.create_index(op.f("ix_mlops_model_aliases_model_id"), "mlops_model_aliases", ["model_id"], unique=False)
    op.create_index(op.f("ix_mlops_model_aliases_project_id"), "mlops_model_aliases", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mlops_model_aliases_project_id"), table_name="mlops_model_aliases")
    op.drop_index(op.f("ix_mlops_model_aliases_model_id"), table_name="mlops_model_aliases")
    op.drop_table("mlops_model_aliases")

    op.drop_index(op.f("ix_mlops_model_versions_run_id"), table_name="mlops_model_versions")
    op.drop_index(op.f("ix_mlops_model_versions_project_id"), table_name="mlops_model_versions")
    op.drop_index(op.f("ix_mlops_model_versions_model_id"), table_name="mlops_model_versions")
    op.drop_index(op.f("ix_mlops_model_versions_artifact_id"), table_name="mlops_model_versions")
    op.drop_table("mlops_model_versions")

    op.drop_index(op.f("ix_mlops_registered_models_project_id"), table_name="mlops_registered_models")
    op.drop_table("mlops_registered_models")

    op.drop_index("ix_mlops_lineage_target", table_name="mlops_artifact_lineage")
    op.drop_index("ix_mlops_lineage_source", table_name="mlops_artifact_lineage")
    op.drop_index(op.f("ix_mlops_artifact_lineage_project_id"), table_name="mlops_artifact_lineage")
    op.drop_table("mlops_artifact_lineage")

    op.drop_index(op.f("ix_mlops_artifacts_sha256"), table_name="mlops_artifacts")
    op.drop_index(op.f("ix_mlops_artifacts_run_id"), table_name="mlops_artifacts")
    op.drop_index(op.f("ix_mlops_artifacts_project_id"), table_name="mlops_artifacts")
    op.drop_table("mlops_artifacts")

    op.drop_index(op.f("ix_mlops_training_runs_sweep_id"), table_name="mlops_training_runs")
    op.drop_index("ix_mlops_training_runs_project_status", table_name="mlops_training_runs")
    op.drop_index(op.f("ix_mlops_training_runs_project_id"), table_name="mlops_training_runs")
    op.drop_index("ix_mlops_training_runs_project_created", table_name="mlops_training_runs")
    op.drop_index(op.f("ix_mlops_training_runs_parent_run_id"), table_name="mlops_training_runs")
    op.drop_index(op.f("ix_mlops_training_runs_dataset_id"), table_name="mlops_training_runs")
    op.drop_table("mlops_training_runs")

    op.drop_index("ix_mlops_sweeps_project_status", table_name="mlops_sweeps")
    op.drop_index(op.f("ix_mlops_sweeps_project_id"), table_name="mlops_sweeps")
    op.drop_table("mlops_sweeps")
