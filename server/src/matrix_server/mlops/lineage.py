"""Bounded, indexed per-hop lineage graph traversal (T6, contract G3 section 3).

Never loads all edges or entities of a project into memory: each BFS hop queries only the
edges and structural foreign keys adjacent to the current frontier within `project_id`,
stopping at `depth` hops or `max_nodes` (hard cap 500) with `truncated=True`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from matrix_server.artifacts.models import Artifact as BlobArtifact
from matrix_server.runs.models import Run
from matrix_server.storage.models import (
    ArtifactLineage,
    BenchmarkRun,
    Dataset,
    DatasetCommit,
    Experiment,
    ModelVersion,
    RegisteredModel,
    SafetyScan,
)
from matrix_server.storage.models import ModelArtifact as MArtifact
from matrix_server.sweeps.models import Sweep

VALID_NODE_KINDS = frozenset(
    {
        "data_version",
        "dataset_commit",
        "dataset",
        "run",
        "model_version",
        "experiment",
        "sweep",
        "benchmark",
        "artifact",
        "safety_scan",
    }
)
MAX_LINEAGE_NODES = 500
DEFAULT_LINEAGE_DEPTH = 3
MAX_LINEAGE_DEPTH = 10
_IN_CHUNK = 250
_STATUS_OUT = {"running": "running", "finished": "completed", "failed": "failed", "killed": "cancelled"}


def _status_out(value: str) -> str:
    return _STATUS_OUT.get(value, value)

Direction = Literal["up", "down", "both"]


def _chunks(items: Iterable[str], size: int = _IN_CHUNK) -> list[list[str]]:
    seq = list(items)
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def parse_node_ref(node: str) -> tuple[str, str]:
    """Parse `<kind>:<id>` into `(kind, entity_id)`. Raises ValueError on invalid syntax/kind."""
    raw = node.strip()
    if ":" not in raw:
        raise ValueError("node must have format '<kind>:<id>'")
    kind, entity_id = raw.split(":", 1)
    kind = kind.strip()
    entity_id = entity_id.strip()
    if kind not in VALID_NODE_KINDS:
        raise ValueError(f"unsupported node kind {kind!r}")
    if not entity_id or len(entity_id) > 64:
        raise ValueError("node id must be 1..64 characters")
    return kind, entity_id


def _edge_types_for_kind(kind: str) -> tuple[str, ...]:
    if kind in ("data_version", "dataset_commit"):
        return ("data_version", "dataset_commit")
    return (kind,)


def node_exists_in_project(db: Session, project_id: str, kind: str, entity_id: str) -> bool:
    """Return True if the node exists as an entity or lineage endpoint in `project_id`."""
    if kind == "run":
        if db.scalar(select(Run.id).where(Run.project_id == project_id, Run.id == entity_id)):
            return True
    elif kind == "model_version":
        if db.scalar(
            select(ModelVersion.id).where(
                ModelVersion.project_id == project_id, ModelVersion.id == entity_id
            )
        ):
            return True
    elif kind == "artifact":
        if db.scalar(
            select(MArtifact.id).where(MArtifact.project_id == project_id, MArtifact.id == entity_id)
        ) or db.scalar(
            select(BlobArtifact.id).where(
                BlobArtifact.project_id == project_id, BlobArtifact.id == entity_id
            )
        ):
            return True
    elif kind in ("data_version", "dataset_commit"):
        if db.scalar(
            select(DatasetCommit.id).where(
                DatasetCommit.project_id == project_id, DatasetCommit.id == entity_id
            )
        ):
            return True
    elif kind == "dataset":
        if db.scalar(select(Dataset.id).where(Dataset.project_id == project_id, Dataset.id == entity_id)):
            return True
    elif kind == "experiment":
        if db.scalar(
            select(Experiment.id).where(Experiment.project_id == project_id, Experiment.id == entity_id)
        ):
            return True
    elif kind == "sweep":
        if db.scalar(select(Sweep.id).where(Sweep.project_id == project_id, Sweep.id == entity_id)):
            return True
    elif kind == "benchmark":
        if db.scalar(
            select(BenchmarkRun.id).where(
                BenchmarkRun.project_id == project_id, BenchmarkRun.id == entity_id
            )
        ):
            return True
    elif kind == "safety_scan" and db.scalar(
        select(SafetyScan.id).where(SafetyScan.project_id == project_id, SafetyScan.id == entity_id)
    ):
        return True

    # Also allow nodes present in materialized lineage edges for this project.
    etypes = _edge_types_for_kind(kind)
    found = db.scalar(
        select(ArtifactLineage.id)
        .where(
            ArtifactLineage.project_id == project_id,
            or_(
                (ArtifactLineage.source_type.in_(etypes)) & (ArtifactLineage.source_id == entity_id),
                (ArtifactLineage.target_type.in_(etypes)) & (ArtifactLineage.target_id == entity_id),
            ),
        )
        .limit(1)
    )
    return found is not None


def _make_edge(
    src_type: str,
    src_id: str,
    tgt_type: str,
    tgt_id: str,
    relation: str,
    *,
    edge_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    src = f"{src_type}:{src_id}"
    tgt = f"{tgt_type}:{tgt_id}"
    return {
        "id": edge_id or f"{src}->{tgt}:{relation}",
        "from": src,
        "to": tgt,
        "kind": relation,
        "source": src,
        "target": tgt,
        "source_type": src_type,
        "source_id": src_id,
        "target_type": tgt_type,
        "target_id": tgt_id,
        "relation": relation,
        "metadata": metadata or {},
    }


def _fetch_hop_edges(
    db: Session,
    project_id: str,
    frontier: list[tuple[str, str]],
    direction: Direction,
    row_limit: int,
) -> list[dict[str, Any]]:
    """Fetch edges adjacent to `frontier` in the requested `direction` using indexed queries only."""
    by_kind: dict[str, set[str]] = {}
    for ntype, nid in frontier:
        by_kind.setdefault(ntype, set()).add(nid)

    edges: list[dict[str, Any]] = []
    go_down = direction in ("down", "both")
    go_up = direction in ("up", "both")

    # 1. Materialized edges in `mlops_artifact_lineage` (indexed on source and target).
    for ntype, ids in by_kind.items():
        etypes = _edge_types_for_kind(ntype)
        for batch in _chunks(ids):
            if go_down:
                rows = db.scalars(
                    select(ArtifactLineage)
                    .where(
                        ArtifactLineage.project_id == project_id,
                        ArtifactLineage.source_type.in_(etypes),
                        ArtifactLineage.source_id.in_(batch),
                    )
                    .limit(row_limit)
                ).all()
                for e in rows:
                    edges.append(
                        _make_edge(
                            e.source_type,
                            e.source_id,
                            e.target_type,
                            e.target_id,
                            e.relation,
                            edge_id=e.id,
                            metadata=dict(e.metadata_ or {}),
                        )
                    )
            if go_up:
                rows = db.scalars(
                    select(ArtifactLineage)
                    .where(
                        ArtifactLineage.project_id == project_id,
                        ArtifactLineage.target_type.in_(etypes),
                        ArtifactLineage.target_id.in_(batch),
                    )
                    .limit(row_limit)
                ).all()
                for e in rows:
                    edges.append(
                        _make_edge(
                            e.source_type,
                            e.source_id,
                            e.target_type,
                            e.target_id,
                            e.relation,
                            edge_id=e.id,
                            metadata=dict(e.metadata_ or {}),
                        )
                    )

    # 2. Structural FK edges per frontier kind (strictly filtered by frontier IDs + project_id).
    if "run" in by_kind:
        run_ids = by_kind["run"]
        for batch in _chunks(run_ids):
            runs = db.scalars(
                select(Run).where(Run.project_id == project_id, Run.id.in_(batch))
            ).all()
            if go_up:
                ds_refs = {r.dataset_version_id for r in runs if r.dataset_version_id}
                commit_ids: set[str] = set()
                if ds_refs:
                    commit_ids = set(
                        db.scalars(
                            select(DatasetCommit.id).where(
                                DatasetCommit.project_id == project_id,
                                DatasetCommit.id.in_(list(ds_refs)),
                            )
                        ).all()
                    )
                for r in runs:
                    if r.dataset_version_id:
                        src_kind = "dataset_commit" if r.dataset_version_id in commit_ids else "dataset"
                        edges.append(_make_edge(src_kind, r.dataset_version_id, "run", r.id, "trained_on"))
                    if r.parent_run_id:
                        edges.append(_make_edge("run", r.parent_run_id, "run", r.id, "forked_from"))
            if go_down:
                for r in runs:
                    if r.sweep_id:
                        edges.append(_make_edge("run", r.id, "sweep", r.sweep_id, "member_of"))
                for child in db.scalars(
                    select(Run)
                    .where(Run.project_id == project_id, Run.parent_run_id.in_(batch))
                    .limit(row_limit)
                ).all():
                    if child.parent_run_id:
                        edges.append(_make_edge("run", child.parent_run_id, "run", child.id, "forked_from"))
                for art in db.scalars(
                    select(MArtifact)
                    .where(MArtifact.project_id == project_id, MArtifact.run_id.in_(batch))
                    .limit(row_limit)
                ).all():
                    if art.run_id:
                        edges.append(_make_edge("run", art.run_id, "artifact", art.id, "produced"))
                for mv in db.scalars(
                    select(ModelVersion)
                    .where(ModelVersion.project_id == project_id, ModelVersion.run_id.in_(batch))
                    .limit(row_limit)
                ).all():
                    if mv.run_id:
                        edges.append(_make_edge("run", mv.run_id, "model_version", mv.id, "registered_as"))

    if "model_version" in by_kind:
        for batch in _chunks(by_kind["model_version"]):
            mvs = db.scalars(
                select(ModelVersion).where(
                    ModelVersion.project_id == project_id, ModelVersion.id.in_(batch)
                )
            ).all()
            for mv in mvs:
                if go_up:
                    if mv.artifact_id:
                        edges.append(_make_edge("artifact", mv.artifact_id, "model_version", mv.id, "packaged_as"))
                    if mv.run_id:
                        edges.append(_make_edge("run", mv.run_id, "model_version", mv.id, "registered_as"))
                if go_down:
                    if mv.eval_experiment_id:
                        edges.append(
                            _make_edge("model_version", mv.id, "experiment", mv.eval_experiment_id, "evaluated_by")
                        )
                    if mv.safety_scan_id:
                        edges.append(
                            _make_edge("model_version", mv.id, "safety_scan", mv.safety_scan_id, "audited_by")
                        )
            if go_down:
                for br in db.scalars(
                    select(BenchmarkRun)
                    .where(
                        BenchmarkRun.project_id == project_id,
                        BenchmarkRun.model_version_id.in_(batch),
                    )
                    .limit(row_limit)
                ).all():
                    if br.model_version_id:
                        edges.append(
                            _make_edge("model_version", br.model_version_id, "benchmark", br.id, "evaluated_by")
                        )

    if "artifact" in by_kind:
        for batch in _chunks(by_kind["artifact"]):
            if go_up:
                for art in db.scalars(
                    select(MArtifact).where(MArtifact.project_id == project_id, MArtifact.id.in_(batch))
                ).all():
                    if art.run_id:
                        edges.append(_make_edge("run", art.run_id, "artifact", art.id, "produced"))
            if go_down:
                for mv in db.scalars(
                    select(ModelVersion)
                    .where(ModelVersion.project_id == project_id, ModelVersion.artifact_id.in_(batch))
                    .limit(row_limit)
                ).all():
                    if mv.artifact_id:
                        edges.append(_make_edge("artifact", mv.artifact_id, "model_version", mv.id, "packaged_as"))

    for ds_kind in ("dataset", "dataset_commit", "data_version"):
        if ds_kind in by_kind and go_down:
            for batch in _chunks(by_kind[ds_kind]):
                for r in db.scalars(
                    select(Run)
                    .where(Run.project_id == project_id, Run.dataset_version_id.in_(batch))
                    .limit(row_limit)
                ).all():
                    if r.dataset_version_id:
                        edges.append(_make_edge(ds_kind, r.dataset_version_id, "run", r.id, "trained_on"))
                if ds_kind == "dataset":
                    br_stmt = select(BenchmarkRun).where(
                        BenchmarkRun.project_id == project_id, BenchmarkRun.dataset_id.in_(batch)
                    )
                else:
                    br_stmt = select(BenchmarkRun).where(
                        BenchmarkRun.project_id == project_id, BenchmarkRun.dataset_commit_id.in_(batch)
                    )
                for br in db.scalars(br_stmt.limit(row_limit)).all():
                    src_id = br.dataset_id if ds_kind == "dataset" else br.dataset_commit_id
                    if src_id:
                        edges.append(_make_edge(ds_kind, src_id, "benchmark", br.id, "evaluated_by"))

    if "sweep" in by_kind and go_up:
        for batch in _chunks(by_kind["sweep"]):
            for r in db.scalars(
                select(Run).where(Run.project_id == project_id, Run.sweep_id.in_(batch)).limit(row_limit)
            ).all():
                if r.sweep_id:
                    edges.append(_make_edge("run", r.id, "sweep", r.sweep_id, "member_of"))

    if "experiment" in by_kind and go_up:
        for batch in _chunks(by_kind["experiment"]):
            for mv in db.scalars(
                select(ModelVersion)
                .where(
                    ModelVersion.project_id == project_id,
                    ModelVersion.eval_experiment_id.in_(batch),
                )
                .limit(row_limit)
            ).all():
                if mv.eval_experiment_id:
                    edges.append(
                        _make_edge("model_version", mv.id, "experiment", mv.eval_experiment_id, "evaluated_by")
                    )

    if "safety_scan" in by_kind and go_up:
        for batch in _chunks(by_kind["safety_scan"]):
            for mv in db.scalars(
                select(ModelVersion)
                .where(
                    ModelVersion.project_id == project_id,
                    ModelVersion.safety_scan_id.in_(batch),
                )
                .limit(row_limit)
            ).all():
                if mv.safety_scan_id:
                    edges.append(
                        _make_edge("model_version", mv.id, "safety_scan", mv.safety_scan_id, "audited_by")
                    )

    if "benchmark" in by_kind and go_up:
        for batch in _chunks(by_kind["benchmark"]):
            for br in db.scalars(
                select(BenchmarkRun).where(BenchmarkRun.project_id == project_id, BenchmarkRun.id.in_(batch))
            ).all():
                if br.model_version_id:
                    edges.append(
                        _make_edge("model_version", br.model_version_id, "benchmark", br.id, "evaluated_by")
                    )
                if br.dataset_commit_id:
                    edges.append(
                        _make_edge("dataset_commit", br.dataset_commit_id, "benchmark", br.id, "evaluated_by")
                    )
                elif br.dataset_id:
                    edges.append(_make_edge("dataset", br.dataset_id, "benchmark", br.id, "evaluated_by"))

    return edges


def _enrich_nodes(
    db: Session,
    project_id: str,
    visited: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Load metadata ONLY for the visited nodes using indexed IN queries."""
    ids_by_kind: dict[str, set[str]] = {}
    for ntype, nid in visited:
        ids_by_kind.setdefault(ntype, set()).add(nid)

    datasets_by_id: dict[str, Dataset] = {}
    if "dataset" in ids_by_kind:
        for batch in _chunks(ids_by_kind["dataset"]):
            for d in db.scalars(select(Dataset).where(Dataset.project_id == project_id, Dataset.id.in_(batch))):
                datasets_by_id[d.id] = d

    commits_by_id: dict[str, DatasetCommit] = {}
    commit_ids_needed = ids_by_kind.get("dataset_commit", set()) | ids_by_kind.get("data_version", set())
    if commit_ids_needed:
        for batch in _chunks(commit_ids_needed):
            for c in db.scalars(
                select(DatasetCommit).where(
                    DatasetCommit.project_id == project_id, DatasetCommit.id.in_(batch)
                )
            ):
                commits_by_id[c.id] = c

    runs_by_id: dict[str, Run] = {}
    if "run" in ids_by_kind:
        for batch in _chunks(ids_by_kind["run"]):
            for r in db.scalars(select(Run).where(Run.project_id == project_id, Run.id.in_(batch))):
                runs_by_id[r.id] = r

    artifacts_by_id: dict[str, MArtifact] = {}
    blobs_by_id: dict[str, BlobArtifact] = {}
    if "artifact" in ids_by_kind:
        for batch in _chunks(ids_by_kind["artifact"]):
            for a in db.scalars(
                select(MArtifact).where(MArtifact.project_id == project_id, MArtifact.id.in_(batch))
            ):
                artifacts_by_id[a.id] = a
            missing = [aid for aid in batch if aid not in artifacts_by_id]
            if missing:
                for b in db.scalars(
                    select(BlobArtifact).where(
                        BlobArtifact.project_id == project_id, BlobArtifact.id.in_(missing)
                    )
                ):
                    blobs_by_id[b.id] = b

    mvs_by_id: dict[str, ModelVersion] = {}
    models_by_id: dict[str, RegisteredModel] = {}
    if "model_version" in ids_by_kind:
        for batch in _chunks(ids_by_kind["model_version"]):
            for m in db.scalars(
                select(ModelVersion).where(
                    ModelVersion.project_id == project_id, ModelVersion.id.in_(batch)
                )
            ):
                mvs_by_id[m.id] = m
        model_ids = {m.model_id for m in mvs_by_id.values()}
        for batch in _chunks(model_ids):
            for rm in db.scalars(
                select(RegisteredModel).where(
                    RegisteredModel.project_id == project_id, RegisteredModel.id.in_(batch)
                )
            ):
                models_by_id[rm.id] = rm

    exps_by_id: dict[str, Experiment] = {}
    if "experiment" in ids_by_kind:
        for batch in _chunks(ids_by_kind["experiment"]):
            for ex in db.scalars(
                select(Experiment).where(Experiment.project_id == project_id, Experiment.id.in_(batch))
            ):
                exps_by_id[ex.id] = ex

    sweeps_by_id: dict[str, Sweep] = {}
    if "sweep" in ids_by_kind:
        for batch in _chunks(ids_by_kind["sweep"]):
            for sw in db.scalars(select(Sweep).where(Sweep.project_id == project_id, Sweep.id.in_(batch))):
                sweeps_by_id[sw.id] = sw

    benchmarks_by_id: dict[str, BenchmarkRun] = {}
    if "benchmark" in ids_by_kind:
        for batch in _chunks(ids_by_kind["benchmark"]):
            for br in db.scalars(
                select(BenchmarkRun).where(
                    BenchmarkRun.project_id == project_id, BenchmarkRun.id.in_(batch)
                )
            ):
                benchmarks_by_id[br.id] = br

    scans_by_id: dict[str, SafetyScan] = {}
    if "safety_scan" in ids_by_kind:
        for batch in _chunks(ids_by_kind["safety_scan"]):
            for sc in db.scalars(
                select(SafetyScan).where(SafetyScan.project_id == project_id, SafetyScan.id.in_(batch))
            ):
                scans_by_id[sc.id] = sc

    nodes: list[dict[str, Any]] = []
    for ntype, nid in sorted(visited):
        label = f"{ntype}:{nid}"
        status: str | None = None
        meta: dict[str, Any] = {}
        if ntype == "dataset" and nid in datasets_by_id:
            ds = datasets_by_id[nid]
            label = ds.name
            meta = {"description": ds.description}
        elif ntype in ("dataset_commit", "data_version") and nid in commits_by_id:
            c = commits_by_id[nid]
            label = f"commit:{c.id[:8]}"
            meta = {"dataset_id": c.dataset_id, "message": c.message, "item_count": c.item_count}
        elif ntype == "run" and nid in runs_by_id:
            r = runs_by_id[nid]
            label = r.name
            status = _status_out(r.status)
            meta = {"run_type": r.run_type, "status": status, "base_model": r.base_model}
        elif ntype == "artifact" and nid in artifacts_by_id:
            a = artifacts_by_id[nid]
            label = f"{a.name}:v{a.version}"
            status = a.status
            meta = {"kind": a.kind, "sha256": a.sha256, "size_bytes": a.size_bytes, "status": a.status}
        elif ntype == "artifact" and nid in blobs_by_id:
            b = blobs_by_id[nid]
            label = b.name
            status = b.status
            meta = {"sha256": b.sha256, "size_bytes": b.size, "status": b.status}
        elif ntype == "model_version" and nid in mvs_by_id:
            mv = mvs_by_id[nid]
            rm = models_by_id.get(mv.model_id)
            label = f"{rm.name if rm else mv.model_id} v{mv.version}"
            status = mv.stage
            meta = {"model_id": mv.model_id, "version": mv.version, "stage": mv.stage}
        elif ntype == "experiment" and nid in exps_by_id:
            ex = exps_by_id[nid]
            label = ex.name
            status = ex.status
            meta = {"status": ex.status}
        elif ntype == "sweep" and nid in sweeps_by_id:
            sw = sweeps_by_id[nid]
            label = sw.name
            status = sw.status
            meta = {"method": (sw.config or {}).get("method"), "status": sw.status}
        elif ntype == "benchmark" and nid in benchmarks_by_id:
            br = benchmarks_by_id[nid]
            label = br.name
            status = br.status
            meta = {"suite": br.suite, "status": br.status, "target_model": br.target_model}
        elif ntype == "safety_scan" and nid in scans_by_id:
            sc = scans_by_id[nid]
            label = f"SafetyScan ({sc.status})"
            status = sc.status
            meta = {"status": sc.status, "global_score": sc.global_score}

        nodes.append(
            {
                "id": f"{ntype}:{nid}",
                "kind": ntype,
                "node_type": ntype,
                "entity_id": nid,
                "label": label,
                "status": status,
                "meta": meta,
                "metadata": meta,
            }
        )
    return nodes


def query_bounded_lineage(
    db: Session,
    project_id: str,
    seed_nodes: list[tuple[str, str]],
    *,
    direction: Direction = "both",
    depth: int = DEFAULT_LINEAGE_DEPTH,
    max_nodes: int = MAX_LINEAGE_NODES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Bounded per-hop BFS over lineage edges and structural FKs within `project_id`.

    Returns `(nodes, edges, truncated)`. At most `max_nodes` (capped at 500) nodes are returned.
    """
    cap = max(1, min(int(max_nodes), MAX_LINEAGE_NODES))
    max_depth = max(0, min(int(depth), MAX_LINEAGE_DEPTH))

    truncated = False
    visited: set[tuple[str, str]] = set()
    frontier: list[tuple[str, str]] = []
    for node in seed_nodes:
        if node in visited:
            continue
        if len(visited) >= cap:
            truncated = True
            break
        visited.add(node)
        frontier.append(node)

    edge_map: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    row_limit = cap + 1

    for _hop in range(max_depth):
        if not frontier:
            break
        hop_edges = _fetch_hop_edges(db, project_id, frontier, direction, row_limit=row_limit)
        next_frontier: list[tuple[str, str]] = []
        frontier_set = set(frontier)
        for edge in hop_edges:
            src = (edge["source_type"], edge["source_id"])
            tgt = (edge["target_type"], edge["target_id"])
            # If the caller seeded `data_version:<id>` and an edge uses `dataset_commit:<id>` (or vice versa),
            # align the endpoint kind with the visited seed so the edge connects cleanly.
            if src not in visited and src[0] in ("data_version", "dataset_commit"):
                alt = ("dataset_commit" if src[0] == "data_version" else "data_version", src[1])
                if alt in visited:
                    src = alt
                    edge = _make_edge(
                        src[0], src[1], tgt[0], tgt[1], edge["relation"], edge_id=edge["id"], metadata=edge["metadata"]
                    )
            if tgt not in visited and tgt[0] in ("data_version", "dataset_commit"):
                alt = ("dataset_commit" if tgt[0] == "data_version" else "data_version", tgt[1])
                if alt in visited:
                    tgt = alt
                    edge = _make_edge(
                        src[0], src[1], tgt[0], tgt[1], edge["relation"], edge_id=edge["id"], metadata=edge["metadata"]
                    )
            key = (src[0], src[1], tgt[0], tgt[1], edge["relation"])
            if key not in edge_map:
                edge_map[key] = edge

            # Determine which endpoint(s) expand along the requested direction.
            candidates: list[tuple[str, str]] = []
            if direction in ("down", "both") and src in frontier_set:
                candidates.append(tgt)
            if direction in ("up", "both") and tgt in frontier_set:
                candidates.append(src)
            for cand in candidates:
                if cand in visited:
                    continue
                if len(visited) >= cap:
                    truncated = True
                    continue
                visited.add(cand)
                next_frontier.append(cand)
        frontier = next_frontier

    nodes = _enrich_nodes(db, project_id, visited)
    edges = [
        edge
        for edge in edge_map.values()
        if (edge["source_type"], edge["source_id"]) in visited
        and (edge["target_type"], edge["target_id"]) in visited
    ]
    edges.sort(key=lambda e: (e["source"], e["target"], e["relation"]))
    return nodes, edges, truncated
