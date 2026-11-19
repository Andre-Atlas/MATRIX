"""T8 (gateway shared state & safety_scans simulated/nullable score) migration fragment.

Not an Alembic revision itself: the coordinator merges parallel fragments into 0009+.
Matches `matrix_server.gateway.models` (`GatewayRateWindow`, `GatewayRequest`) and the T8
updates to `matrix_server.storage.models.SafetyScan` (verified by `tests/test_t8_migration.py`).
Requires `safety_scans` (present since 0007/0008).
"""

import sqlalchemy as sa
from alembic import op

TABLES = ("gateway_rate_windows", "gateway_requests")


def upgrade() -> None:
    op.create_table(
        "gateway_rate_windows",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "scope", "window_start"),
    )
    op.create_index(
        "ix_gateway_rate_windows_window_start",
        "gateway_rate_windows",
        ["window_start"],
    )

    op.create_table(
        "gateway_requests",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("route", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False),
        sa.Column("error_sanitized", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gateway_requests_project_ts",
        "gateway_requests",
        ["project_id", "ts"],
    )
    op.create_index(
        "ix_gateway_requests_ts",
        "gateway_requests",
        ["ts"],
    )

    with op.batch_alter_table("safety_scans") as batch_op:
        batch_op.add_column(
            sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.alter_column(
            "global_score",
            existing_type=sa.Float(),
            nullable=True,
        )


def downgrade() -> None:
    op.execute(sa.text("UPDATE safety_scans SET global_score = 0.0 WHERE global_score IS NULL"))
    with op.batch_alter_table("safety_scans") as batch_op:
        batch_op.alter_column(
            "global_score",
            existing_type=sa.Float(),
            nullable=False,
        )
        batch_op.drop_column("simulated")

    for table in reversed(TABLES):
        op.drop_table(table)
