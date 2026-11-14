"""sota completion tables and media extension

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. DatasetItem.media extension
    with op.batch_alter_table("dataset_items") as batch_op:
        batch_op.add_column(sa.Column("media", sa.JSON(), nullable=False, server_default="[]"))

    # 2. Zero-Copy Data Versioning
    op.create_table(
        "dataset_branches",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("head_commit_id", sa.String(length=32), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "name", name="uq_dataset_branches_dataset_name"),
    )
    op.create_index(op.f("ix_dataset_branches_dataset_id"), "dataset_branches", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_dataset_branches_project_id"), "dataset_branches", ["project_id"], unique=False)

    op.create_table(
        "dataset_commits",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("parent_commit_id", sa.String(length=32), nullable=True),
        sa.Column("second_parent_commit_id", sa.String(length=32), nullable=True),
        sa.Column("tree_hash", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dataset_commits_dataset_id"), "dataset_commits", ["dataset_id"], unique=False)
    op.create_index("ix_dataset_commits_project_created", "dataset_commits", ["project_id", "created_at"], unique=False)
    op.create_index(op.f("ix_dataset_commits_project_id"), "dataset_commits", ["project_id"], unique=False)

    op.create_table(
        "dataset_commit_items",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("commit_id", sa.String(length=32), nullable=False),
        sa.Column("item_key", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("media", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("artifact_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["commit_id"], ["dataset_commits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commit_id", "item_key", name="uq_dataset_commit_items_commit_item_key"),
    )
    op.create_index(op.f("ix_dataset_commit_items_commit_id"), "dataset_commit_items", ["commit_id"], unique=False)
    op.create_index(op.f("ix_dataset_commit_items_content_hash"), "dataset_commit_items", ["content_hash"], unique=False)
    op.create_index(op.f("ix_dataset_commit_items_project_id"), "dataset_commit_items", ["project_id"], unique=False)

    # 3. Benchmarks
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("suite", sa.String(length=32), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("runner_backend", sa.String(length=32), nullable=False, server_default="local_sim"),
        sa.Column("runner_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("eval_mode", sa.String(length=32), nullable=False, server_default="generate_until"),
        sa.Column("num_fewshot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_model", sa.String(length=200), nullable=False),
        sa.Column("model_version_id", sa.String(length=32), nullable=True),
        sa.Column("dataset_id", sa.String(length=32), nullable=True),
        sa.Column("dataset_commit_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("summary_metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_version_id"], ["mlops_model_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_benchmark_runs_dataset_id"), "benchmark_runs", ["dataset_id"], unique=False)
    op.create_index(op.f("ix_benchmark_runs_model_version_id"), "benchmark_runs", ["model_version_id"], unique=False)
    op.create_index("ix_benchmark_runs_project_created", "benchmark_runs", ["project_id", "created_at"], unique=False)
    op.create_index(op.f("ix_benchmark_runs_project_id"), "benchmark_runs", ["project_id"], unique=False)
    op.create_index("ix_benchmark_runs_project_status", "benchmark_runs", ["project_id", "status"], unique=False)

    op.create_table(
        "benchmark_task_results",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("benchmark_run_id", sa.String(length=32), nullable=False),
        sa.Column("task_name", sa.String(length=200), nullable=False),
        sa.Column("modality", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column("eval_mode", sa.String(length=32), nullable=False, server_default="generate_until"),
        sa.Column("num_fewshot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("primary_metric", sa.String(length=100), nullable=False),
        sa.Column("primary_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("latency_ms_p50", sa.Float(), nullable=True),
        sa.Column("latency_ms_p95", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["benchmark_run_id"], ["benchmark_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("benchmark_run_id", "task_name", name="uq_benchmark_task_results_run_task"),
    )
    op.create_index(op.f("ix_benchmark_task_results_benchmark_run_id"), "benchmark_task_results", ["benchmark_run_id"], unique=False)
    op.create_index(op.f("ix_benchmark_task_results_project_id"), "benchmark_task_results", ["project_id"], unique=False)

    # 4. Inspect AI Sandboxes
    op.create_table(
        "sandbox_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("runner_backend", sa.String(length=32), nullable=False, server_default="local_sandbox"),
        sa.Column("dataset_id", sa.String(length=32), nullable=True),
        sa.Column("dataset_commit_id", sa.String(length=32), nullable=True),
        sa.Column("solver_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("scorer_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sandbox_policy", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("pass_rate", sa.Float(), nullable=True),
        sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sandbox_runs_dataset_id"), "sandbox_runs", ["dataset_id"], unique=False)
    op.create_index("ix_sandbox_runs_project_created", "sandbox_runs", ["project_id", "created_at"], unique=False)
    op.create_index(op.f("ix_sandbox_runs_project_id"), "sandbox_runs", ["project_id"], unique=False)
    op.create_index("ix_sandbox_runs_project_status", "sandbox_runs", ["project_id", "status"], unique=False)

    op.create_table(
        "sandbox_task_transcripts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("sandbox_run_id", sa.String(length=32), nullable=False),
        sa.Column("task_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="passed"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("scorer_details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("verified_artifacts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sandbox_run_id"], ["sandbox_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sandbox_run_id", "task_key", name="uq_sandbox_task_transcripts_run_task"),
    )
    op.create_index(op.f("ix_sandbox_task_transcripts_project_id"), "sandbox_task_transcripts", ["project_id"], unique=False)
    op.create_index(op.f("ix_sandbox_task_transcripts_sandbox_run_id"), "sandbox_task_transcripts", ["sandbox_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sandbox_task_transcripts_sandbox_run_id"), table_name="sandbox_task_transcripts")
    op.drop_index(op.f("ix_sandbox_task_transcripts_project_id"), table_name="sandbox_task_transcripts")
    op.drop_table("sandbox_task_transcripts")

    op.drop_index("ix_sandbox_runs_project_status", table_name="sandbox_runs")
    op.drop_index(op.f("ix_sandbox_runs_project_id"), table_name="sandbox_runs")
    op.drop_index("ix_sandbox_runs_project_created", table_name="sandbox_runs")
    op.drop_index(op.f("ix_sandbox_runs_dataset_id"), table_name="sandbox_runs")
    op.drop_table("sandbox_runs")

    op.drop_index(op.f("ix_benchmark_task_results_project_id"), table_name="benchmark_task_results")
    op.drop_index(op.f("ix_benchmark_task_results_benchmark_run_id"), table_name="benchmark_task_results")
    op.drop_table("benchmark_task_results")

    op.drop_index("ix_benchmark_runs_project_status", table_name="benchmark_runs")
    op.drop_index(op.f("ix_benchmark_runs_project_id"), table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_project_created", table_name="benchmark_runs")
    op.drop_index(op.f("ix_benchmark_runs_model_version_id"), table_name="benchmark_runs")
    op.drop_index(op.f("ix_benchmark_runs_dataset_id"), table_name="benchmark_runs")
    op.drop_table("benchmark_runs")

    op.drop_index(op.f("ix_dataset_commit_items_project_id"), table_name="dataset_commit_items")
    op.drop_index(op.f("ix_dataset_commit_items_content_hash"), table_name="dataset_commit_items")
    op.drop_index(op.f("ix_dataset_commit_items_commit_id"), table_name="dataset_commit_items")
    op.drop_table("dataset_commit_items")

    op.drop_index(op.f("ix_dataset_commits_project_id"), table_name="dataset_commits")
    op.drop_index("ix_dataset_commits_project_created", table_name="dataset_commits")
    op.drop_index(op.f("ix_dataset_commits_dataset_id"), table_name="dataset_commits")
    op.drop_table("dataset_commits")

    op.drop_index(op.f("ix_dataset_branches_project_id"), table_name="dataset_branches")
    op.drop_index(op.f("ix_dataset_branches_dataset_id"), table_name="dataset_branches")
    op.drop_table("dataset_branches")

    with op.batch_alter_table("dataset_items") as batch_op:
        batch_op.drop_column("media")
