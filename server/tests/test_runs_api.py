"""Runs API (contract G1) on SQLite/PostgreSQL with the memory and ClickHouse metric stores."""

import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.runs import service
from matrix_server.runs.models import Run, RunTag
from matrix_server.runs.store import ClickHouseMetricStore, MemoryMetricStore
from matrix_server.storage.db import Base, make_engine, utcnow
from matrix_server.storage.models import AuditLog, Job
from matrix_server.workers import run_handlers, runner

ADMIN = {"Authorization": "Bearer adm"}
PASSWORD = "correct-horse-battery-staple"
PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
CLICKHOUSE_URL = os.environ.get("MATRIX_TEST_CLICKHOUSE_URL")


@dataclass
class Proj:
    id: str
    key: dict
    owner: dict  # user session headers (owner role)


@pytest.fixture(scope="module")
def ch_store():
    if not CLICKHOUSE_URL:
        pytest.skip("MATRIX_TEST_CLICKHOUSE_URL not set")
    store = ClickHouseMetricStore(CLICKHOUSE_URL, f"matrix_test_runsapi_{uuid.uuid4().hex[:12]}")
    store.ensure_schema()
    yield store
    store.drop_database()


@pytest.fixture(params=["sqlite-memory", "sqlite-clickhouse", "postgres-clickhouse"])
def client(request, tmp_path):
    db, store_kind = request.param.split("-")
    if db == "sqlite":
        url = f"sqlite:///{tmp_path}/m.db"
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        url = PG_URL
        eng = make_engine(url)
        Base.metadata.drop_all(eng)
        eng.dispose()
    store = MemoryMetricStore() if store_kind == "memory" else request.getfixturevalue("ch_store")
    app = create_app(
        Settings(database_url=url, admin_token="adm", artifact_local_dir=str(tmp_path / "artifacts"))
    )
    app.state.metric_store = store
    run_handlers.set_metric_store(store)
    with TestClient(app) as c:
        yield c
    run_handlers.set_metric_store(None)
    app.state.db_engine.dispose()
    if db == "postgres":
        eng = make_engine(url)
        Base.metadata.drop_all(eng)
        eng.dispose()


def make_project(client, name) -> Proj:
    email = f"{name}-{uuid.uuid4().hex[:6]}@example.com"
    resp = client.post("/api/v1/admin/users", json={"email": email, "name": name, "password": PASSWORD}, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    resp = client.post("/api/v1/admin/projects", json={"name": name, "owner_email": email}, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    pid, key = resp.json()["project"]["id"], resp.json()["api_key"]
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    owner = {"Authorization": f"Bearer {resp.json()['token']}", "X-Matrix-Project": pid}
    return Proj(pid, {"Authorization": f"Bearer {key}"}, owner)


@pytest.fixture
def two(client):
    return make_project(client, "one"), make_project(client, "two")


def new_run(client, proj, **body):
    resp = client.post("/api/v1/runs", json=body, headers=proj.key)
    assert resp.status_code == 201, resp.text
    return resp.json()


def log(client, proj, run_id, points):
    resp = client.post(f"/api/v1/runs/{run_id}/metrics", json={"points": points}, headers=proj.key)
    assert resp.status_code == 202, resp.text
    return resp.json()


def wait_until(check, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.2)
    return check()


def make_artifact(client, proj, name="model.bin") -> str:
    resp = client.post(
        "/api/v1/artifacts",
        content=b"weights",
        headers={**proj.key, "X-Filename": name, "Content-Type": "application/octet-stream"},
    )
    if resp.status_code == 201:
        return resp.json()["id"]
    # Artifact API not available: insert the row directly (same columns as the G0 table).
    artifact_id = uuid.uuid4().hex
    factory = client.app.state.session_factory
    with factory() as s, s.begin():
        s.execute(
            insert(service.ARTIFACTS).values(
                id=artifact_id, project_id=proj.id, name=name, media_type="application/octet-stream", size=7,
                sha256="0" * 64, verified=True, status="ready", created_at=utcnow(),
            )
        )
    return artifact_id


# --------------------------------------------------------------------------- runs


def test_requires_auth_and_roles(client, two):
    one, _ = two
    assert client.get("/api/v1/runs").status_code == 401
    assert client.post("/api/v1/runs", json={}).status_code == 401
    resp = client.post(f"/api/v1/projects/{one.id}/keys", json={"name": "ro", "scopes": ["read"]}, headers=one.owner)
    assert resp.status_code == 201, resp.text
    reader = {"Authorization": f"Bearer {resp.json()['api_key']}"}
    run = new_run(client, one)
    assert client.get("/api/v1/runs", headers=reader).status_code == 200
    assert client.get(f"/api/v1/runs/{run['id']}", headers=reader).status_code == 200
    assert client.post("/api/v1/runs", json={}, headers=reader).status_code == 403
    assert client.post(f"/api/v1/runs/{run['id']}/metrics", json={"points": []}, headers=reader).status_code == 403
    # Keys are never admin: deleting a run needs an admin/owner session.
    assert client.delete(f"/api/v1/runs/{run['id']}", headers=one.key).status_code == 403


def test_create_get_and_generated_names(client, two):
    one, _ = two
    run = new_run(
        client,
        one,
        group="sweep-a",
        tags=["baseline", "gpu", "gpu"],
        params={"lr": 0.001, "layers": [64, 64]},
        git={"commit": "abc123", "branch": "main", "dirty": False, "remote": "git@x:y.git"},
        env={"python": "3.12.1", "platform": "linux", "hostname": "box", "packages": ["torch==2.5.0"]},
        sweep_id="sw1",
        dataset_version_id="dv1",
    )
    assert run["status"] == "running" and run["ended_at"] is None and run["error"] is None
    assert run["tags"] == ["baseline", "gpu"] and run["params"]["layers"] == [64, 64]
    assert run["git"]["commit"] == "abc123" and run["env"]["packages"] == ["torch==2.5.0"]
    assert run["summary"] == {} and run["started_at"].endswith("Z")
    parts = run["name"].split("-")
    assert len(parts) == 3 and parts[2] == "1"
    second = new_run(client, one, name="custom", parent_run_id=run["id"])
    assert second["name"] == "custom" and second["parent_run_id"] == run["id"]
    assert new_run(client, one)["name"].endswith("-3")
    got = client.get(f"/api/v1/runs/{run['id']}", headers=one.key).json()
    assert got["id"] == run["id"] and set(got) >= {"summary", "heartbeat_at", "created_by", "sweep_id"}


def test_create_validation(client, two):
    one, two_ = two
    bad = [
        {"tags": ["x"] * 51},
        {"tags": ["bad\ttag"]},
        {"name": ""},
        {"env": {"packages": ["p==1"] * 501}},
        {"parent_run_id": "nope"},
    ]
    for body in bad:
        assert client.post("/api/v1/runs", json=body, headers=one.key).status_code == 422, body
    foreign = new_run(client, two_)
    resp = client.post("/api/v1/runs", json={"parent_run_id": foreign["id"]}, headers=one.key)
    assert resp.status_code == 422
    # The string "nan" is an ordinary param value; a JSON NaN literal is rejected, and the 422 does
    # not echo the input (NaN would not serialize).
    assert client.post("/api/v1/runs", json={"params": {"lr": "nan"}}, headers=one.key).status_code == 201
    resp = client.post(
        "/api/v1/runs", content=b'{"params": {"x": NaN}}', headers={**one.key, "Content-Type": "application/json"}
    )
    assert resp.status_code == 422


def test_list_filters_search_and_summary(client, two):
    one, two_ = two
    a = new_run(client, one, name="alpha", group="g1", tags=["base"], sweep_id="s1")
    b = new_run(client, one, name="beta", group="g2", tags=["Tuned_x"])
    c = new_run(client, one, name="gamma", group="g1")
    new_run(client, two_, name="alpha", group="g1", tags=["base"])
    client.patch(f"/api/v1/runs/{c['id']}", json={"status": "finished"}, headers=one.key)
    log(client, one, a["id"], [{"key": "loss", "step": 1, "value": 0.5}, {"key": "loss", "step": 2, "value": 0.25}])
    log(client, one, a["id"], [{"key": "sys/cpu.percent", "step": 1, "value": 10}])
    log(client, one, b["id"], [{"key": "acc", "step": 0, "value": "nan"}])

    def names(**params):
        resp = client.get("/api/v1/runs", params=params, headers=one.key)
        assert resp.status_code == 200, resp.text
        return [r["name"] for r in resp.json()["items"]]

    assert names() == ["gamma", "beta", "alpha"]
    assert names(sort="started_at") == ["alpha", "beta", "gamma"]
    assert names(sort="name") == ["alpha", "beta", "gamma"]
    assert names(sort="-name") == ["gamma", "beta", "alpha"]
    assert names(status="finished") == ["gamma"]
    assert names(status="running") == ["beta", "alpha"]
    assert sorted(names(group="g1")) == ["alpha", "gamma"]
    assert names(tag="base") == ["alpha"]
    assert names(tag="bas") == []
    assert names(name="beta") == ["beta"]
    assert names(sweep_id="s1") == ["alpha"]
    assert names(q="ALP") == ["alpha"]
    assert names(q="g2") == ["beta"]
    assert names(q="tuned_") == ["beta"]  # matches the tag; `_` is literal
    assert names(q="%") == []
    items = {r["name"]: r for r in client.get("/api/v1/runs", headers=one.key).json()["items"]}
    assert items["alpha"]["summary"] == {"loss": 0.25}
    assert items["beta"]["summary"] == {"acc": None}
    assert client.get(f"/api/v1/runs/{a['id']}", headers=one.key).json()["summary"] == {"loss": 0.25}
    assert client.get("/api/v1/runs", params={"status": "zombie"}, headers=one.key).status_code == 422
    assert client.get("/api/v1/runs", params={"sort": "error"}, headers=one.key).status_code == 422


def test_pagination_is_stable(client, two):
    one, _ = two
    ids = [new_run(client, one, name=f"r{i:02d}")["id"] for i in range(7)]
    # Equal start times exercise the id tie-breaker.
    factory = client.app.state.session_factory
    with factory() as s, s.begin():
        s.execute(update(Run).where(Run.id.in_(ids[:4])).values(started_at=utcnow() - timedelta(hours=1)))
    for sort in ("-started_at", "started_at", "name", "-name"):
        seen, cursor = [], None
        while True:
            params = {"limit": 2, "sort": sort, **({"cursor": cursor} if cursor else {})}
            page = client.get("/api/v1/runs", params=params, headers=one.key).json()
            seen += [r["id"] for r in page["items"]]
            # A run created while paging never duplicates or reorders what was already seen.
            if len(seen) == 2 and sort == "-started_at":
                new_run(client, one, name="late")
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) and set(ids) <= set(seen), sort
    resp = client.get("/api/v1/runs", params={"limit": 2, "sort": "name"}, headers=one.key).json()
    assert client.get(
        "/api/v1/runs", params={"cursor": resp["next_cursor"], "sort": "-started_at"}, headers=one.key
    ).status_code == 400
    assert client.get("/api/v1/runs", params={"cursor": "!!bad"}, headers=one.key).status_code == 400
    assert client.get("/api/v1/runs", params={"limit": 201}, headers=one.key).status_code == 422


def test_patch_status_and_tags(client, two):
    one, _ = two
    run = new_run(client, one, tags=["a"])
    rid = run["id"]
    assert client.patch(f"/api/v1/runs/{rid}", json={"status": "running"}, headers=one.key).status_code == 422
    resp = client.patch(f"/api/v1/runs/{rid}", json={"name": "renamed", "tags": ["b", "c"]}, headers=one.key)
    assert resp.status_code == 200 and resp.json()["name"] == "renamed" and resp.json()["tags"] == ["b", "c"]
    assert [r["id"] for r in client.get("/api/v1/runs", params={"tag": "a"}, headers=one.key).json()["items"]] == []
    assert len(client.get("/api/v1/runs", params={"tag": "c"}, headers=one.key).json()["items"]) == 1
    resp = client.patch(f"/api/v1/runs/{rid}", json={"status": "failed", "error": "OOM"}, headers=one.key)
    out = resp.json()
    assert out["status"] == "failed" and out["error"] == "OOM" and out["ended_at"] is not None
    ended = out["ended_at"]
    again = client.patch(f"/api/v1/runs/{rid}", json={"status": "killed"}, headers=one.key).json()
    assert again["status"] == "killed" and again["ended_at"] == ended


def test_params_are_immutable(client, two):
    one, _ = two
    rid = new_run(client, one, params={"lr": 0.1, "opt": "adam"})["id"]
    url = f"/api/v1/runs/{rid}/params"
    resp = client.post(url, json={"params": {"lr": 0.1, "batch": 32}}, headers=one.key)
    assert resp.status_code == 200 and resp.json()["params"] == {"lr": 0.1, "opt": "adam", "batch": 32}
    resp = client.post(url, json={"params": {"lr": 0.2, "opt": "sgd", "new": 1}}, headers=one.key)
    assert resp.status_code == 409
    assert resp.json()["conflicts"] == ["lr", "opt"]
    # Nothing applied from a conflicting request; type changes are conflicts too.
    assert "new" not in client.get(f"/api/v1/runs/{rid}", headers=one.key).json()["params"]
    assert client.post(url, json={"params": {"batch": 32.0}}, headers=one.key).status_code == 409
    assert client.post(url, json={"params": {"batch": "32"}}, headers=one.key).status_code == 409
    resp = client.post(
        url, content=b'{"params": {"x": Infinity}}', headers={**one.key, "Content-Type": "application/json"}
    )
    assert resp.status_code == 422  # ...but a JSON Infinity/NaN literal is rejected
    assert client.post(url, json={}, headers=one.key).status_code == 422


def test_heartbeat(client, two):
    one, _ = two
    rid = new_run(client, one)["id"]
    before = client.get(f"/api/v1/runs/{rid}", headers=one.key).json()["heartbeat_at"]
    time.sleep(0.01)
    assert client.post(f"/api/v1/runs/{rid}/heartbeat", headers=one.key).status_code == 204
    assert client.get(f"/api/v1/runs/{rid}", headers=one.key).json()["heartbeat_at"] > before


# --------------------------------------------------------------------------- metrics


def test_metrics_ingest_validation_and_partial_acceptance(client, two):
    one, _ = two
    rid = new_run(client, one)["id"]
    points = [
        {"key": "loss", "step": 0, "value": 1.5},
        {"key": "sys/gpu.0.utilization", "step": 0, "value": 99},
        {"key": "bad key!", "step": 0, "value": 1},
        {"key": "x" * 201, "step": 0, "value": 1},
        {"key": "loss", "step": -1, "value": 1},
        {"key": "loss", "step": 1.5, "value": 1},
        {"key": "loss", "step": True, "value": 1},
        {"key": "loss", "step": 2**53, "value": 1},
        {"key": "loss", "step": 3, "value": "NaN?"},
        {"key": "loss", "step": 3, "value": True},
        {"key": "loss", "step": 3, "value": None},
        {"key": "loss", "step": 4, "value": 1, "ts": "yesterday"},
        {"key": "loss", "step": 4, "value": 1, "ts": "1960-01-01T00:00:00Z"},
        {"key": "loss", "step": 5, "value": "nan"},
        {"key": "loss", "step": 6, "value": "inf"},
        {"key": "loss", "step": 7, "value": "-inf", "ts": "2026-11-10T12:00:00+02:00"},
        {"key": "loss", "step": 8, "value": 1, "extra": 1},
        "not an object",
        {"key": "a/b.c:d@e f-g_h", "step": 0, "value": 0},
    ]
    out = log(client, one, rid, points)
    assert out["accepted"] == 6
    assert [r["index"] for r in out["rejected"]] == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 17]
    assert all(isinstance(r["reason"], str) and r["reason"] for r in out["rejected"])
    series = client.get(f"/api/v1/runs/{rid}/metrics", params={"keys": "loss"}, headers=one.key).json()
    loss = series["series"]["loss"]
    assert loss["steps"] == [0, 5, 6, 7] and loss["values"] == [1.5, None, None, None]
    assert loss["ts"][3] == "2026-11-10T10:00:00.000000Z"
    # Request-level limits.
    too_many = [{"key": "k", "step": i, "value": 1} for i in range(10_001)]
    assert client.post(f"/api/v1/runs/{rid}/metrics", json={"points": too_many}, headers=one.key).status_code == 422
    assert client.post(f"/api/v1/runs/{rid}/metrics", json={"points": "x"}, headers=one.key).status_code == 422
    big = {"points": [{"key": "k" * 200, "step": 1, "value": 1}] * 9000}  # ~2.1 MB
    assert client.post(f"/api/v1/runs/{rid}/metrics", json=big, headers=one.key).status_code == 413
    assert client.post("/api/v1/runs/nope/metrics", json={"points": []}, headers=one.key).status_code == 404


def test_metric_caps(client, two):
    one, _ = two
    client.app.state.settings.max_points_per_run = 5
    client.app.state.settings.max_points_per_key = 3
    try:
        rid = new_run(client, one)["id"]
        out = log(client, one, rid, [{"key": "a", "step": i, "value": i} for i in range(4)])
        assert out["accepted"] == 3 and [r["index"] for r in out["rejected"]] == [3]
        out = log(client, one, rid, [{"key": "b", "step": i, "value": i} for i in range(3)])
        assert out["accepted"] == 2 and "limit" in out["rejected"][0]["reason"]
    finally:
        client.app.state.settings.max_points_per_run = 50_000_000
        client.app.state.settings.max_points_per_key = 10_000_000


def test_metric_keys_series_downsampling_and_resend(client, two):
    one, _ = two
    rid = new_run(client, one)["id"]
    steps = list(range(0, 30_000, 3))  # 10 000 points, uniform gaps
    for i in range(0, len(steps), 5000):
        chunk = steps[i : i + 5000]
        log(client, one, rid, [{"key": "train/loss", "step": s, "value": 1.0 / (s + 1)} for s in chunk])
    log(client, one, rid, [{"key": "train/loss", "step": 10_000, "value": 50.0}])  # spike, off-grid step
    log(client, one, rid, [{"key": "train/loss", "step": 9_999, "value": 0.5}])  # on-grid: replaces, not adds
    log(client, one, rid, [{"key": "acc", "step": s, "value": s / 10} for s in (0, 1, 7, 100)])
    log(client, one, rid, [{"key": "acc", "step": 7, "value": 0.05}])  # resend: last write wins
    keys = client.get(f"/api/v1/runs/{rid}/metric-keys", headers=one.key).json()["keys"]
    assert keys[0] == {
        "key": "acc", "count": 4, "first_step": 0, "last_step": 100, "last_value": 10.0, "min": 0.0, "max": 10.0,
    }
    assert keys[1]["key"] == "train/loss" and keys[1]["count"] == 10_001 and keys[1]["max"] == 50.0
    url = f"/api/v1/runs/{rid}/metrics"
    full = client.get(url, params={"keys": "acc"}, headers=one.key).json()
    assert full["downsampled"] is False
    assert full["series"]["acc"]["steps"] == [0, 1, 7, 100] and full["series"]["acc"]["values"][2] == 0.05
    resp = client.get(url, params={"keys": "train/loss,acc,missing", "max_points": 200}, headers=one.key).json()
    loss = resp["series"]["train/loss"]
    assert resp["downsampled"] is True
    assert len(loss["steps"]) <= 200 and loss["steps"] == sorted(loss["steps"])
    assert 50.0 in loss["values"] and loss["steps"][0] == 0 and loss["steps"][-1] == 29_997
    assert len(loss["ts"]) == len(loss["values"]) == len(loss["steps"])
    assert resp["series"]["missing"] == {"steps": [], "values": [], "ts": []}
    implicit = client.get(url, params={"max_points": 200}, headers=one.key).json()
    assert set(implicit["series"]) == {"acc", "train/loss"}
    window = client.get(url, params={"keys": "train/loss", "from_step": 30, "to_step": 60}, headers=one.key).json()
    assert window["series"]["train/loss"]["steps"] == list(range(30, 61, 3))
    by_time = client.get(url, params={"keys": "train/loss", "x": "time", "max_points": 100}, headers=one.key).json()
    assert by_time["series"]["train/loss"]["ts"] == sorted(by_time["series"]["train/loss"]["ts"])
    for params in ({"max_points": 3}, {"max_points": 5001}, {"x": "epoch"}, {"from_step": -1}):
        assert client.get(url, params={"keys": "acc", **params}, headers=one.key).status_code == 422


def test_closed_run_grace(client, two):
    one, _ = two
    rid = new_run(client, one)["id"]
    client.patch(f"/api/v1/runs/{rid}", json={"status": "finished"}, headers=one.key)
    assert log(client, one, rid, [{"key": "late", "step": 1, "value": 1}])["accepted"] == 1
    factory = client.app.state.session_factory
    with factory() as s, s.begin():
        s.execute(update(Run).where(Run.id == rid).values(ended_at=utcnow() - timedelta(minutes=11)))
    resp = client.post(f"/api/v1/runs/{rid}/metrics", json={"points": [{"key": "late", "step": 2, "value": 1}]},
                       headers=one.key)
    assert resp.status_code == 409


def test_compare(client, two):
    one, two_ = two
    a, b = new_run(client, one, name="a"), new_run(client, one, name="b")
    foreign = new_run(client, two_, name="secret")
    for run, scale in ((a, 1.0), (b, 2.0)):
        log(client, one, run["id"], [{"key": "loss", "step": s, "value": scale * s} for s in range(50)])
    log(client, two_, foreign["id"], [{"key": "loss", "step": 0, "value": 123.0}])
    ids = ",".join([a["id"], b["id"], foreign["id"], "unknown"])
    resp = client.get("/api/v1/runs/compare", params={"run_ids": ids, "keys": "loss,acc", "max_points": 20},
                      headers=one.key)
    assert resp.status_code == 200, resp.text
    runs = resp.json()["runs"]
    assert set(runs) == {a["id"], b["id"]}  # foreign and unknown ids are left out
    assert runs[a["id"]]["name"] == "a" and set(runs[a["id"]]["series"]) == {"loss", "acc"}
    assert len(runs[b["id"]]["series"]["loss"]["steps"]) <= 20 and max(runs[b["id"]]["series"]["loss"]["values"]) == 98
    assert runs[a["id"]]["series"]["acc"]["steps"] == []
    many = ",".join(f"r{i}" for i in range(21))
    assert client.get("/api/v1/runs/compare", params={"run_ids": many, "keys": "loss"}, headers=one.key).status_code == 422
    keys21 = ",".join(f"k{i}" for i in range(21))
    assert client.get("/api/v1/runs/compare", params={"run_ids": a["id"], "keys": keys21},
                      headers=one.key).status_code == 422
    assert client.get("/api/v1/runs/compare", params={"run_ids": a["id"], "keys": ""}, headers=one.key).status_code == 422


def test_project_isolation_on_every_endpoint(client, two):
    one, two_ = two
    rid = new_run(client, one, params={"p": 1})["id"]
    log(client, one, rid, [{"key": "loss", "step": 0, "value": 1}])
    rep = client.post(f"/api/v1/runs/{rid}/reports", json={"kind": "custom", "name": "r", "data": {}}, headers=one.key)
    report_id = rep.json()["id"]
    k = two_.key
    for method, path, body in [
        ("get", f"/api/v1/runs/{rid}", None),
        ("patch", f"/api/v1/runs/{rid}", {"name": "x"}),
        ("post", f"/api/v1/runs/{rid}/params", {"params": {"p": 2}}),
        ("post", f"/api/v1/runs/{rid}/heartbeat", None),
        ("post", f"/api/v1/runs/{rid}/metrics", {"points": [{"key": "loss", "step": 1, "value": 9}]}),
        ("get", f"/api/v1/runs/{rid}/metric-keys", None),
        ("get", f"/api/v1/runs/{rid}/metrics", None),
        ("get", f"/api/v1/runs/{rid}/artifacts", None),
        ("post", f"/api/v1/runs/{rid}/artifacts", {"artifact_id": "a1", "name": "m", "kind": "model"}),
        ("get", f"/api/v1/runs/{rid}/reports", None),
        ("post", f"/api/v1/runs/{rid}/reports", {"kind": "custom", "name": "r", "data": {}}),
        ("delete", f"/api/v1/runs/{rid}/reports/{report_id}", None),
        ("delete", f"/api/v1/runs/{rid}", None),
    ]:
        headers = two_.owner if path == f"/api/v1/runs/{rid}" and method == "delete" else k
        resp = client.request(method, path, json=body, headers=headers)
        assert resp.status_code == 404, (method, path, resp.status_code)
    assert client.get("/api/v1/runs", headers=k).json()["items"] == []
    assert client.get("/api/v1/runs", params={"q": "o"}, headers=k).json()["items"] == []
    # Nothing changed in project one.
    run = client.get(f"/api/v1/runs/{rid}", headers=one.key).json()
    assert run["params"] == {"p": 1} and run["summary"] == {"loss": 1.0}
    assert len(client.get(f"/api/v1/runs/{rid}/reports", headers=one.key).json()["items"]) == 1


# --------------------------------------------------------------------------- artifacts and reports


def test_artifact_links(client, two):
    one, two_ = two
    rid = new_run(client, one)["id"]
    mine = make_artifact(client, one)
    theirs = make_artifact(client, two_)
    url = f"/api/v1/runs/{rid}/artifacts"
    resp = client.post(url, json={"artifact_id": mine, "name": "best", "kind": "model"}, headers=one.key)
    assert resp.status_code == 201, resp.text
    assert resp.json()["artifact"]["id"] == mine and resp.json()["kind"] == "model"
    # Re-link (SDK retry) updates instead of duplicating.
    resp = client.post(url, json={"artifact_id": mine, "name": "final", "kind": "file"}, headers=one.key)
    assert resp.status_code == 201
    assert client.post(url, json={"artifact_id": theirs, "name": "x", "kind": "file"}, headers=one.key).status_code == 404
    assert client.post(url, json={"artifact_id": "missing", "name": "x", "kind": "file"},
                       headers=one.key).status_code == 404
    assert client.post(url, json={"artifact_id": mine, "name": "x", "kind": "weights"},
                       headers=one.key).status_code == 422
    items = client.get(url, headers=one.key).json()["items"]
    assert len(items) == 1 and items[0]["name"] == "final" and items[0]["kind"] == "file"
    assert set(items[0]["artifact"]) == {
        "id", "name", "media_type", "size", "sha256", "verified", "owner_kind", "owner_id", "created_at", "created_by",
    }


VALID_CLASSIFICATION = {
    "labels": ["cat", "dog"],
    "metrics": {"accuracy": 0.9, "f1_macro": 0.88},
    "per_class": [
        {"label": "cat", "precision": 0.9, "recall": 0.8, "f1": 0.85, "support": 10},
        {"label": "dog", "precision": 0.8, "recall": 0.9, "f1": 0.85, "support": 12},
    ],
    "confusion_matrix": [[8, 2], [1, 11]],
    "roc": [{"label": "dog", "fpr": [0, 0.5, 1], "tpr": [0, 0.9, 1], "auc": 0.9}],
    "pr": [{"label": "dog", "precision": [1, 0.8], "recall": [0, 1], "ap": 0.85}],
}


def test_reports_crud(client, two):
    one, _ = two
    rid = new_run(client, one)["id"]
    url = f"/api/v1/runs/{rid}/reports"
    resp = client.post(url, json={"kind": "classification", "name": "eval", "data": VALID_CLASSIFICATION},
                       headers=one.key)
    assert resp.status_code == 201, resp.text
    report = resp.json()
    assert report["kind"] == "classification" and report["data"]["labels"] == ["cat", "dog"]
    client.post(url, json={"kind": "custom", "name": "notes", "data": {"free": ["json", 1]}}, headers=one.key)
    bad = dict(VALID_CLASSIFICATION, confusion_matrix=[[1, 2]])
    resp = client.post(url, json={"kind": "classification", "name": "eval", "data": bad}, headers=one.key)
    assert resp.status_code == 422
    assert client.post(url, json={"kind": "nope", "name": "x", "data": {}}, headers=one.key).status_code == 422
    resp = client.post(url, content=b'{"kind": "custom", "name": "x", "data": {"v": NaN}}',
                       headers={**one.key, "Content-Type": "application/json"})
    assert resp.status_code == 422
    assert [r["kind"] for r in client.get(url, headers=one.key).json()["items"]] == ["classification", "custom"]
    assert [r["name"] for r in client.get(url, params={"kind": "custom"}, headers=one.key).json()["items"]] == ["notes"]
    assert client.delete(f"{url}/{report['id']}", headers=one.key).status_code == 204
    assert client.delete(f"{url}/{report['id']}", headers=one.key).status_code == 404
    assert [r["kind"] for r in client.get(url, headers=one.key).json()["items"]] == ["custom"]


# --------------------------------------------------------------------------- delete


def test_delete_run_removes_everything(client, two):
    one, _ = two
    parent = new_run(client, one, tags=["t"])
    child = new_run(client, one, parent_run_id=parent["id"])
    rid = parent["id"]
    log(client, one, rid, [{"key": "loss", "step": s, "value": s} for s in range(10)])
    client.post(f"/api/v1/runs/{rid}/reports", json={"kind": "custom", "name": "r", "data": {}}, headers=one.key)
    client.post(f"/api/v1/runs/{rid}/artifacts", json={"artifact_id": make_artifact(client, one), "name": "m",
                                                       "kind": "model"}, headers=one.key)
    assert client.delete(f"/api/v1/runs/{rid}", headers=one.owner).status_code == 204
    assert client.get(f"/api/v1/runs/{rid}", headers=one.key).status_code == 404
    assert client.delete(f"/api/v1/runs/{rid}", headers=one.owner).status_code == 404
    assert client.get(f"/api/v1/runs/{child['id']}", headers=one.key).json()["parent_run_id"] is None
    factory = client.app.state.session_factory
    with factory() as s:
        assert s.scalars(select(RunTag).where(RunTag.run_id == rid)).all() == []
        audit_rows = s.scalars(select(AuditLog).where(AuditLog.action == "run.delete")).all()
        assert [a.target for a in audit_rows] == [rid]
        purge = s.scalars(select(Job).where(Job.kind == service.PURGE_KIND)).all()
        assert [j.payload for j in purge] == [{"run_id": rid}]
    store = client.app.state.metric_store
    assert wait_until(lambda: store.read_series(one.id, [rid], ["loss"])[0] == {})
    # The purge job is idempotent (the inline delete already ran).
    with factory() as s, s.begin():
        s.execute(update(Job).where(Job.kind == service.REAPER_KIND).values(run_after=utcnow() + timedelta(hours=1)))
    runner.load_handler_modules(["matrix_server.workers.run_handlers"])
    assert runner.run_once(factory, "w")
    with factory() as s:
        assert s.scalars(select(Job.status).where(Job.kind == service.PURGE_KIND)).all() == ["done"]


def test_one_million_points_through_the_api(client, two, request):
    if "clickhouse" not in request.node.callspec.params["client"] or "sqlite" not in request.node.callspec.params["client"]:
        pytest.skip("perf check runs once, on SQLite + ClickHouse")
    one, _ = two
    rid = new_run(client, one)["id"]
    started = time.perf_counter()
    for i in range(0, 1_000_000, 10_000):
        out = log(client, one, rid, [{"key": "loss", "step": s, "value": math.exp(-s / 1e5)} for s in range(i, i + 10_000)])
        assert out["accepted"] == 10_000
    ingest = time.perf_counter() - started
    url = f"/api/v1/runs/{rid}/metrics"
    client.get(url, params={"keys": "loss", "max_points": 1000}, headers=one.key)  # warm-up
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        resp = client.get(url, params={"keys": "loss", "max_points": 1000}, headers=one.key)
        timings.append(time.perf_counter() - started)
    assert resp.status_code == 200 and resp.json()["downsampled"] is True
    assert len(resp.json()["series"]["loss"]["steps"]) <= 1000
    print(f"\nAPI 1M points: ingest {ingest:.1f}s ({1e6 / ingest:,.0f} pts/s), read best {min(timings) * 1000:.0f} ms")
    assert min(timings) < 1.0
    client.app.state.metric_store.delete_run(one.id, rid)
