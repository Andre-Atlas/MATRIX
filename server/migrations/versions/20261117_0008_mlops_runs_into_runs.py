"""main's MLOps runs and artifacts onto runs / artifacts (T4, ADR-013)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17 00:00:00

Upgrade:
- `runs` gains `run_type` and `base_model` (nullable).
- Every `mlops_training_runs` row becomes a `runs` row with the same id (`hyperparams` -> `params`,
  status completed->finished, cancelled/stopped->killed, queued->running, `dataset_id` ->
  `dataset_version_id`, `hardware_info` -> `env["hardware"]`, `finished_at` -> `ended_at`,
  `_last_heartbeat_at` -> `heartbeat_at`, tags -> `run_tags`), `classic_eval` becomes `run_reports`
  named `classic_eval` (inlined `classic_eval_to_reports`) and `summary_metrics` +
  `total_steps` are kept as a `custom` report `mlops_summary` (the points themselves live in
  ClickHouse `training_metrics`, which Alembic does not reach: see t4-notes.md for the copy into
  `run_metrics`). Rows whose project no longer exists are dropped (orphans).
- `mlops_artifacts` becomes `model_artifacts` (metadata; `artifact_id` -> `artifacts.id` or
  `external_uri`). External rows keep their URI. Blobs of `main`'s own store are not moved: those
  rows keep `artifact_id` null and record the old location in `metadata.legacy_storage_*`.
- `mlops_model_versions.run_id` / `.artifact_id` now reference `runs` / `model_artifacts`.
- `mlops_training_runs` and `mlops_artifacts` are dropped.

Downgrade recreates both tables and copies back what fits: runs with a `run_type` (the MLOps
ones) go to `mlops_training_runs` too (they also stay in `runs`, which exists at 0007) with
`summary_metrics` from the `mlops_summary` report and `classic_eval` rebuilt from the reports;
model artifacts go back to `mlops_artifacts` (an artifact uploaded through the G0 store gets
`storage_backend="artifact"`, `storage_uri="artifact:<id>"`, which the 0007 code cannot serve).
Lost on downgrade: `run_type`/`base_model` of runs created through `/api/v1/runs`, and metric
points written after the upgrade (they are in `run_metrics`, not `training_metrics`).
This revision targets PostgreSQL (like the whole chain; CI runs it there).
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

from matrix_server.runs.reports import MAX_CURVE_POINTS, ReportError, validate_report
from matrix_server.storage.db import new_id

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUMMARY_REPORT = "mlops_summary"
CLASSIC_EVAL_REPORT = "classic_eval"
_SCALARS = ("roc_auc", "pr_auc", "accuracy", "f1", "precision", "recall", "log_loss")
STATUS_IN = {
    "running": "running",
    "queued": "running",
    "completed": "finished",
    "finished": "finished",
    "failed": "failed",
    "cancelled": "killed",
    "stopped": "killed",
    "killed": "killed",
}
STATUS_OUT = {"running": "running", "finished": "completed", "failed": "failed", "killed": "cancelled"}


def status_in(value: str) -> str | None:
    return STATUS_IN.get(value)


def status_out(value: str) -> str:
    return STATUS_OUT.get(value, value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _thin(values: list[Any], limit: int = MAX_CURVE_POINTS) -> list[Any]:
    if len(values) <= limit:
        return values
    idx = sorted({round(i * (len(values) - 1) / (limit - 1)) for i in range(limit)})
    return [values[i] for i in idx]


def _classification(ce: dict[str, Any]) -> tuple[dict[str, Any] | None, set[str], dict[str, Any]]:
    cm = ce.get("confusion_matrix")
    if not isinstance(cm, Mapping) or set(cm) - {"labels", "matrix"}:
        return None, set(), {}
    labels = [str(label) for label in cm.get("labels") or []]
    metrics = {k: float(ce[k]) for k in _SCALARS if _is_number(ce.get(k))}
    data: dict[str, Any] = {
        "labels": labels,
        "metrics": metrics,
        "per_class": [],
        "confusion_matrix": cm.get("matrix"),
        "roc": [],
        "pr": [],
    }
    consumed = {"confusion_matrix", *metrics}
    extra: dict[str, Any] = {}
    curve = ce.get("roc_curve")
    auc = ce.get("roc_auc")
    if isinstance(curve, list) and curve and labels and _is_number(auc) and all(isinstance(p, Mapping) for p in curve):
        pts = _thin(list(curve))
        data["roc"] = [
            {"label": labels[-1], "fpr": [p.get("fpr") for p in pts], "tpr": [p.get("tpr") for p in pts], "auc": auc}
        ]
        consumed.add("roc_curve")
        if len(pts) != len(curve) or any(set(p) - {"fpr", "tpr"} for p in curve):
            extra["roc_curve"] = curve
    return data, consumed, extra


def _importance(ce: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    items = ce.get("feature_importance")
    if not isinstance(items, list) or not items or not all(isinstance(i, Mapping) for i in items):
        return None, {}
    data = {
        "method": "model",
        "features": [{"name": str(i.get("feature")), "importance": i.get("importance")} for i in items],
    }
    directions = {str(i.get("feature")): i["direction"] for i in items if i.get("direction") is not None}
    extra = {"feature_importance_direction": directions} if directions else {}
    if any(set(i) - {"feature", "importance", "direction"} for i in items):
        extra = {"feature_importance": items}
    return data, extra


def classic_eval_to_reports(classic_eval: Mapping[str, Any] | None) -> list[tuple[str, str, Any]]:
    if not classic_eval:
        return []
    ce = dict(classic_eval)
    reports: list[tuple[str, str, Any]] = []
    rest = dict(ce)
    cls, consumed, extra_cls = _classification(ce)
    if cls is not None:
        try:
            reports.append(("classification", CLASSIC_EVAL_REPORT, validate_report("classification", cls)))
            for key in consumed:
                rest.pop(key, None)
            rest.update(extra_cls)
        except ReportError:
            pass
    imp, extra_imp = _importance(ce)
    if imp is not None:
        try:
            reports.append(("importance", CLASSIC_EVAL_REPORT, validate_report("importance", imp)))
            rest.pop("feature_importance", None)
            rest.update(extra_imp)
        except ReportError:
            pass
    if rest:
        reports.append(("custom", CLASSIC_EVAL_REPORT, validate_report("custom", rest)))
    return reports


def reports_to_classic_eval(reports: Iterable[tuple[str, Any]]) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    seen = False
    for kind, data in reports:
        seen = True
        if kind == "classification":
            out.update(data.get("metrics") or {})
            out["confusion_matrix"] = {"labels": data.get("labels"), "matrix": data.get("confusion_matrix")}
            roc = data.get("roc") or []
            if roc:
                out["roc_curve"] = [{"fpr": f, "tpr": t} for f, t in zip(roc[0]["fpr"], roc[0]["tpr"], strict=False)]
        elif kind == "importance":
            out["feature_importance"] = [
                {"feature": f["name"], "importance": f["importance"]} for f in data.get("features") or []
            ]
        elif kind == "custom" and isinstance(data, Mapping):
            custom = dict(data)
    directions = custom.pop("feature_importance_direction", None)
    out.update(custom)
    if isinstance(directions, Mapping):
        for item in out.get("feature_importance") or []:
            if item.get("feature") in directions:
                item["direction"] = directions[item["feature"]]
    return out if seen else None

_runs = sa.table(
    "runs",
    sa.column("id", sa.String),
    sa.column("project_id", sa.String),
    sa.column("name", sa.String),
    sa.column("status", sa.String),
    sa.column("group_name", sa.String),
    sa.column("tags", sa.JSON),
    sa.column("params", sa.JSON),
    sa.column("git", sa.JSON),
    sa.column("env", sa.JSON),
    sa.column("dataset_version_id", sa.String),
    sa.column("run_type", sa.String),
    sa.column("base_model", sa.String),
    sa.column("sweep_id", sa.String),
    sa.column("parent_run_id", sa.String),
    sa.column("created_by", sa.String),
    sa.column("started_at", sa.DateTime(timezone=True)),
    sa.column("ended_at", sa.DateTime(timezone=True)),
    sa.column("heartbeat_at", sa.DateTime(timezone=True)),
    sa.column("error", sa.Text),
    sa.column("point_count", sa.BigInteger),
    sa.column("key_counts", sa.JSON),
)
_run_tags = sa.table(
    "run_tags", sa.column("id", sa.String), sa.column("run_id", sa.String),
    sa.column("project_id", sa.String), sa.column("tag", sa.String),
)
_run_reports = sa.table(
    "run_reports", sa.column("id", sa.String), sa.column("run_id", sa.String), sa.column("project_id", sa.String),
    sa.column("kind", sa.String), sa.column("name", sa.String), sa.column("data", sa.JSON),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
_projects = sa.table("projects", sa.column("id", sa.String))
_datasets = sa.table("datasets", sa.column("id", sa.String))
_sweeps = sa.table("mlops_sweeps", sa.column("id", sa.String))


def _old_runs_table() -> list[Any]:
    return [
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
    ]


_OLD_ARTIFACT_COLUMNS = [
    ("id", sa.String(32), False), ("project_id", sa.String(32), False), ("run_id", sa.String(32), True),
    ("name", sa.String(200), False), ("kind", sa.String(32), False), ("version", sa.Integer(), False),
    ("storage_backend", sa.String(16), False), ("storage_uri", sa.String(1000), False),
    ("sha256", sa.String(64), True), ("size_bytes", sa.BigInteger(), True), ("content_type", sa.String(200), True),
    ("status", sa.String(24), False), ("metadata", sa.JSON(), False),
    ("created_at", sa.DateTime(timezone=True), False), ("committed_at", sa.DateTime(timezone=True), True),
]


def _table(name: str, cols: list[tuple[str, Any, bool]] | list[Any]) -> sa.TableClause:
    if cols and isinstance(cols[0], tuple):
        return sa.table(name, *(sa.column(c, t) for c, t, _ in cols))
    return sa.table(name, *(sa.column(c.name, c.type) for c in cols))


def _fk_name(table: str, column: str) -> str | None:
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
        if fk["constrained_columns"] == [column]:
            return fk["name"]
    return None


def _repoint_fk(table: str, column: str, target: str, name: str) -> None:
    old = _fk_name(table, column)
    if old:
        op.drop_constraint(old, table, type_="foreignkey")
    op.create_foreign_key(name, table, target, [column], ["id"], ondelete="SET NULL")


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _create_model_artifacts() -> None:
    op.create_table(
        "model_artifacts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("artifact_id", sa.String(length=32), nullable=True),
        sa.Column("external_uri", sa.String(length=1000), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_type", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_model_artifacts_name_version"),
    )
    op.create_index(op.f("ix_model_artifacts_artifact_id"), "model_artifacts", ["artifact_id"], unique=False)
    op.create_index(op.f("ix_model_artifacts_project_id"), "model_artifacts", ["project_id"], unique=False)
    op.create_index(op.f("ix_model_artifacts_run_id"), "model_artifacts", ["run_id"], unique=False)
    op.create_index(op.f("ix_model_artifacts_sha256"), "model_artifacts", ["sha256"], unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("runs", sa.Column("run_type", sa.String(length=32), nullable=True))
    op.add_column("runs", sa.Column("base_model", sa.String(length=200), nullable=True))

    # --- runs
    old_runs = _table("mlops_training_runs", _old_runs_table())
    projects = {r[0] for r in bind.execute(sa.select(_projects.c.id))}
    existing = {r[0] for r in bind.execute(sa.select(_runs.c.id))}
    rows = [dict(r._mapping) for r in bind.execute(sa.select(old_runs).order_by(old_runs.c.created_at))]
    rows = [r for r in rows if r["project_id"] in projects and r["id"] not in existing]
    copied = {r["id"] for r in rows}
    now = datetime.now(UTC)
    run_rows, tag_rows, report_rows = [], [], []
    for r in rows:
        summary = dict(r["summary_metrics"] or {})
        heartbeat = _parse_ts(summary.pop("_last_heartbeat_at", None))
        status = status_in(r["status"]) or "failed"
        started = r["started_at"] or r["created_at"]
        tags = [t for t in dict.fromkeys(r["tags"] or []) if isinstance(t, str) and 0 < len(t) <= 100]
        run_rows.append(
            {
                "id": r["id"], "project_id": r["project_id"], "name": r["name"], "status": status,
                "group_name": None, "tags": tags, "params": dict(r["hyperparams"] or {}), "git": None,
                "env": {"hardware": r["hardware_info"]} if r["hardware_info"] else None,
                "dataset_version_id": r["dataset_id"], "run_type": r["run_type"], "base_model": r["base_model"],
                "sweep_id": r["sweep_id"], "parent_run_id": None, "created_by": None,
                "started_at": started,
                "ended_at": r["finished_at"] or (now if status != "running" else None),
                "heartbeat_at": heartbeat or started, "error": r["error"], "point_count": 0, "key_counts": {},
            }
        )
        tag_rows += [{"id": new_id(), "run_id": r["id"], "project_id": r["project_id"], "tag": t} for t in tags]
        reports = classic_eval_to_reports(r["classic_eval"])
        if summary or r["total_steps"] is not None:
            reports.append(("custom", SUMMARY_REPORT, {"summary_metrics": summary, "total_steps": r["total_steps"]}))
        report_rows += [
            {"id": new_id(), "run_id": r["id"], "project_id": r["project_id"], "kind": kind, "name": name,
             "data": data, "created_at": r["created_at"]}
            for kind, name, data in reports
        ]
    if run_rows:
        op.bulk_insert(_runs, run_rows)
    if tag_rows:
        op.bulk_insert(_run_tags, tag_rows)
    if report_rows:
        op.bulk_insert(_run_reports, report_rows)
    for r in rows:  # after every run exists (self-reference)
        if r["parent_run_id"] in copied or r["parent_run_id"] in existing:
            bind.execute(sa.update(_runs).where(_runs.c.id == r["id"]).values(parent_run_id=r["parent_run_id"]))

    # --- artifacts
    _create_model_artifacts()
    old_art = _table("mlops_artifacts", _OLD_ARTIFACT_COLUMNS)
    new_art = sa.table(
        "model_artifacts", *(sa.column(c, t) for c, t, _ in _OLD_ARTIFACT_COLUMNS if c not in ("storage_backend", "storage_uri")),
        sa.column("artifact_id", sa.String), sa.column("external_uri", sa.String),
    )
    valid_runs = existing | copied
    art_rows = []
    for a in (dict(r._mapping) for r in bind.execute(sa.select(old_art))):
        if a["project_id"] not in projects:
            continue
        backend, uri = a.pop("storage_backend"), a.pop("storage_uri")
        meta = dict(a["metadata"] or {})
        external = uri if backend == "external" else None
        if external is None:
            meta.update(legacy_storage_backend=backend, legacy_storage_uri=uri)
        art_rows.append(
            {**a, "metadata": meta, "external_uri": external, "artifact_id": None,
             "run_id": a["run_id"] if a["run_id"] in valid_runs else None}
        )
    if art_rows:
        op.bulk_insert(new_art, art_rows)

    # --- registry FKs, then the old tables go
    mv = sa.table("mlops_model_versions", sa.column("run_id", sa.String), sa.column("artifact_id", sa.String))
    kept_art = {a["id"] for a in art_rows}
    bind.execute(sa.update(mv).where(mv.c.run_id.is_not(None), mv.c.run_id.not_in(sorted(valid_runs) or [""])).values(run_id=None))
    bind.execute(sa.update(mv).where(mv.c.artifact_id.is_not(None), mv.c.artifact_id.not_in(sorted(kept_art) or [""])).values(artifact_id=None))
    _repoint_fk("mlops_model_versions", "artifact_id", "model_artifacts", "mlops_model_versions_artifact_id_fkey")
    _repoint_fk("mlops_model_versions", "run_id", "runs", "mlops_model_versions_run_id_fkey")

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


def downgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "mlops_training_runs",
        *_old_runs_table(),
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
        *(sa.Column(c, t, nullable=n) for c, t, n in _OLD_ARTIFACT_COLUMNS),
        sa.ForeignKeyConstraint(["run_id"], ["mlops_training_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_mlops_artifacts_name_version"),
    )
    op.create_index(op.f("ix_mlops_artifacts_project_id"), "mlops_artifacts", ["project_id"], unique=False)
    op.create_index(op.f("ix_mlops_artifacts_run_id"), "mlops_artifacts", ["run_id"], unique=False)
    op.create_index(op.f("ix_mlops_artifacts_sha256"), "mlops_artifacts", ["sha256"], unique=False)

    # --- runs with a run_type (the MLOps ones) back into mlops_training_runs
    datasets = {r[0] for r in bind.execute(sa.select(_datasets.c.id))}
    sweeps = {r[0] for r in bind.execute(sa.select(_sweeps.c.id))}
    runs = [dict(r._mapping) for r in bind.execute(sa.select(_runs).where(_runs.c.run_type.is_not(None)))]
    ids = {r["id"] for r in runs}
    reports: dict[str, list[tuple[str, str, Any]]] = {}
    if ids:
        for rr in bind.execute(
            sa.select(_run_reports.c.run_id, _run_reports.c.kind, _run_reports.c.name, _run_reports.c.data)
            .where(_run_reports.c.run_id.in_(sorted(ids)), _run_reports.c.name.in_(["classic_eval", SUMMARY_REPORT]))
            .order_by(_run_reports.c.created_at)
        ):
            reports.setdefault(rr.run_id, []).append((rr.kind, rr.name, rr.data))
    old_runs = _table("mlops_training_runs", _old_runs_table())
    out = []
    for r in runs:
        mine = reports.get(r["id"], [])
        summary_data = next((d for k, n, d in mine if n == SUMMARY_REPORT and isinstance(d, dict)), {})
        summary = dict(summary_data.get("summary_metrics") or {})
        if r["heartbeat_at"] is not None:
            summary["_last_heartbeat_at"] = r["heartbeat_at"].isoformat()
        env = r["env"] if isinstance(r["env"], dict) else {}
        out.append(
            {
                "id": r["id"], "project_id": r["project_id"], "name": r["name"], "run_type": r["run_type"],
                "status": status_out(r["status"]), "base_model": r["base_model"],
                "dataset_id": r["dataset_version_id"] if r["dataset_version_id"] in datasets else None,
                "sweep_id": r["sweep_id"] if r["sweep_id"] in sweeps else None, "parent_run_id": None,
                "hyperparams": dict(r["params"] or {}), "summary_metrics": summary,
                "hardware_info": env.get("hardware"),
                "classic_eval": reports_to_classic_eval([(k, d) for k, n, d in mine if n == "classic_eval"]),
                "tags": list(r["tags"] or []), "total_steps": summary_data.get("total_steps"), "error": r["error"],
                "created_at": r["started_at"], "started_at": r["started_at"], "finished_at": r["ended_at"],
            }
        )
    if out:
        op.bulk_insert(old_runs, out)
    for r in runs:
        if r["parent_run_id"] in ids:
            bind.execute(sa.update(old_runs).where(old_runs.c.id == r["id"]).values(parent_run_id=r["parent_run_id"]))

    # --- model artifacts back into mlops_artifacts
    new_art = sa.table(
        "model_artifacts", *(sa.column(c, t) for c, t, _ in _OLD_ARTIFACT_COLUMNS if c not in ("storage_backend", "storage_uri")),
        sa.column("artifact_id", sa.String), sa.column("external_uri", sa.String),
    )
    arts = []
    for a in (dict(r._mapping) for r in bind.execute(sa.select(new_art))):
        meta = dict(a["metadata"] or {})
        artifact_id, external = a.pop("artifact_id"), a.pop("external_uri")
        if external:
            backend, uri = "external", external
        elif artifact_id:
            backend, uri = "artifact", f"artifact:{artifact_id}"
        else:
            backend = meta.pop("legacy_storage_backend", "local")
            uri = meta.pop("legacy_storage_uri", "")
        arts.append({**a, "metadata": meta, "storage_backend": backend, "storage_uri": uri,
                     "run_id": a["run_id"] if a["run_id"] in ids else None})
    if arts:
        op.bulk_insert(_table("mlops_artifacts", _OLD_ARTIFACT_COLUMNS), arts)

    mv = sa.table("mlops_model_versions", sa.column("run_id", sa.String))
    bind.execute(sa.update(mv).where(mv.c.run_id.is_not(None), mv.c.run_id.not_in(sorted(ids) or [""])).values(run_id=None))
    _repoint_fk("mlops_model_versions", "artifact_id", "mlops_artifacts", "mlops_model_versions_artifact_id_fkey")
    _repoint_fk("mlops_model_versions", "run_id", "mlops_training_runs", "mlops_model_versions_run_id_fkey")

    op.drop_index(op.f("ix_model_artifacts_sha256"), table_name="model_artifacts")
    op.drop_index(op.f("ix_model_artifacts_run_id"), table_name="model_artifacts")
    op.drop_index(op.f("ix_model_artifacts_project_id"), table_name="model_artifacts")
    op.drop_index(op.f("ix_model_artifacts_artifact_id"), table_name="model_artifacts")
    op.drop_table("model_artifacts")
    op.drop_column("runs", "base_model")
    op.drop_column("runs", "run_type")
