"""Tests for T6: Model Registry (unique production, stage history, signed webhooks via egress_client,
model version files), Bounded Lineage (<= 500 nodes, depth, direction), Datasets commit cap & run link,
RBAC, cross-project isolation, and migration fragment `t6_registry.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from rbac_helpers import project_with_roles
from sqlalchemy import select

from matrix_server import egress
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.mlops.registry_models import ModelWebhook
from matrix_server.storage.db import Base, make_engine
from matrix_server.storage.models import (
    ArtifactLineage,
    AuditLog,
    BenchmarkRun,
    Experiment,
    Job,
    ModelVersion,
    RegisteredModel,
)
from matrix_server.workers import runner as worker_runner
from matrix_server.workers import webhook_handlers

SECRET_KEY = "t6-test-master-secret-key-for-fernet"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/t6.db",
        span_store="memory",
        artifact_backend="local",
        artifact_local_dir=str(tmp_path / "artifacts"),
        secret_key=SECRET_KEY,
        max_dataset_commit_items=5,
    )
    app = create_app(settings)
    client = TestClient(app)
    factory = app.state.session_factory
    pid_a, roles_a = project_with_roles(factory, "T6 Project A")
    pid_b, roles_b = project_with_roles(factory, "T6 Project B")
    try:
        yield {
            "app": app,
            "client": client,
            "factory": factory,
            "pid_a": pid_a,
            "pid_b": pid_b,
            "admin_a": roles_a["admin"],
            "member_a": roles_a["member"],
            "viewer_a": roles_a["viewer"],
            "admin_b": roles_b["admin"],
            "member_b": roles_b["member"],
            "viewer_b": roles_b["viewer"],
        }
    finally:
        app.state.db_engine.dispose()


def _create_model(client: TestClient, headers: dict[str, str], name: str = "demo-model") -> dict[str, Any]:
    resp = client.post("/api/v1/mlops/models", headers=headers, json={"name": name, "model_type": "lora"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_version(
    client: TestClient, headers: dict[str, str], model_id: str, **extra: Any
) -> dict[str, Any]:
    resp = client.post(f"/api/v1/mlops/models/{model_id}/versions", headers=headers, json=extra)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Unique production per model + stage history + RBAC + audit
# ---------------------------------------------------------------------------


def test_unique_production_demotes_previous_and_records_history(env):
    client: TestClient = env["client"]
    admin_a = env["admin_a"]
    member_a = env["member_a"]
    viewer_a = env["viewer_a"]
    admin_b = env["admin_b"]

    model = _create_model(client, member_a, "prod-unique-model")
    mid = model["id"]
    _create_version(client, member_a, mid)
    _create_version(client, member_a, mid)

    # Member can move v1 to staging, cannot promote to production or archive
    assert (
        client.post(
            f"/api/v1/models/{mid}/versions/1/stage",
            headers=member_a,
            json={"stage": "staging", "comment": "ready for QA"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/models/{mid}/versions/1/stage",
            headers=member_a,
            json={"stage": "production"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/models/{mid}/versions/1/stage",
            headers=member_a,
            json={"stage": "archived"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/models/{mid}/versions/1/stage",
            headers=viewer_a,
            json={"stage": "staging"},
        ).status_code
        == 403
    )

    # Admin promotes v1 to production
    r1 = client.post(
        f"/api/v1/models/{mid}/versions/1/stage",
        headers=admin_a,
        json={"stage": "production", "comment": "initial prod rollout"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["stage"] == "production"

    # Admin promotes v2 to production -> v1 automatically demoted to archived in same transaction
    r2 = client.patch(
        f"/api/v1/mlops/models/{mid}/versions/2/stage",
        headers=admin_a,
        json={"stage": "production", "comment": "v2 outperforms v1"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["stage"] == "production"

    # Verify v1 is now archived and only v2 is in production
    got = client.get(f"/api/v1/mlops/models/{mid}", headers=viewer_a).json()
    stages_by_ver = {v["version"]: v["stage"] for v in got["versions"]}
    assert stages_by_ver == {1: "archived", 2: "production"}

    # Creating v3 directly in production demotes v2 to archived
    v3 = _create_version(client, admin_a, mid, stage="production")
    assert v3["stage"] == "production"
    got2 = client.get(f"/api/v1/mlops/models/{mid}", headers=viewer_a).json()
    stages_by_ver2 = {v["version"]: v["stage"] for v in got2["versions"]}
    assert stages_by_ver2 == {1: "archived", 2: "archived", 3: "production"}

    # History endpoint (both /mlops/models/{id}/history and /models/{id}/history)
    hist_resp = client.get(f"/api/v1/models/{mid}/history", headers=viewer_a)
    assert hist_resp.status_code == 200, hist_resp.text
    items = hist_resp.json()["items"]
    transitions = [(i["version"], i["from_stage"], i["to_stage"]) for i in items]
    assert (1, "none", "staging") in transitions
    assert (1, "staging", "production") in transitions
    assert (1, "production", "archived") in transitions
    assert (2, "none", "production") in transitions
    assert (2, "production", "archived") in transitions
    assert (3, "none", "production") in transitions

    # Filter history by version
    v1_hist = client.get(f"/api/v1/mlops/models/{mid}/history?version=1", headers=viewer_a)
    assert v1_hist.status_code == 200
    assert all(i["version"] == 1 for i in v1_hist.json()["items"])

    # Cross-project isolation
    assert client.get(f"/api/v1/models/{mid}/history", headers=admin_b).status_code == 404

    # Audit log contains model.stage_change entries
    with env["factory"]() as db:
        actions = list(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.project_id == env["pid_a"], AuditLog.action == "model.stage_change"
                )
            )
        )
    assert len(actions) >= 3


# ---------------------------------------------------------------------------
# 2. Aliases validation, PUT/DELETE, RBAC, audit
# ---------------------------------------------------------------------------


def test_aliases_validation_rbac_and_delete(env):
    client: TestClient = env["client"]
    admin_a = env["admin_a"]
    member_a = env["member_a"]
    viewer_a = env["viewer_a"]

    mid = _create_model(client, member_a, "alias-model")["id"]
    _create_version(client, member_a, mid)
    _create_version(client, member_a, mid)

    # Invalid alias names -> 422
    for bad in ("1starts-digit", "UpperCase", "bad/slash", "has space", "a" * 51):
        resp = client.post(
            f"/api/v1/mlops/models/{mid}/aliases", headers=admin_a, json={"alias": bad, "version": 1}
        )
        assert resp.status_code == 422, bad
    assert (
        client.put(f"/api/v1/models/{mid}/aliases/1starts-digit", headers=admin_a, json={"version": 1}).status_code
        == 422
    )

    # Valid non-admin alias via PUT (member allowed, viewer forbidden)
    assert (
        client.put(f"/api/v1/models/{mid}/aliases/challenger_1", headers=viewer_a, json={"version": 1}).status_code
        == 403
    )
    ok = client.put(f"/api/v1/models/{mid}/aliases/challenger_1", headers=member_a, json={"version": 1})
    assert ok.status_code == 200, ok.text
    assert ok.json()["alias"] == "challenger_1"

    # champion requires ADMIN for both PUT and DELETE
    assert (
        client.put(f"/api/v1/models/{mid}/aliases/champion", headers=member_a, json={"version": 2}).status_code == 403
    )
    assert (
        client.put(f"/api/v1/models/{mid}/aliases/champion", headers=admin_a, json={"version": 2}).status_code == 200
    )
    assert client.delete(f"/api/v1/models/{mid}/aliases/champion", headers=member_a).status_code == 403
    assert client.delete(f"/api/v1/models/{mid}/aliases/champion", headers=admin_a).status_code == 200

    # Audit log recorded alias set and delete
    with env["factory"]() as db:
        actions = set(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.project_id == env["pid_a"],
                    AuditLog.action.in_(["model.alias_set", "model.alias_delete"]),
                )
            )
        )
    assert actions == {"model.alias_set", "model.alias_delete"}


# ---------------------------------------------------------------------------
# 3. Webhooks: secret key guard, Fernet at rest, HMAC, egress allowlist & redirect SSRF
# ---------------------------------------------------------------------------


def test_webhook_refused_without_secret_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MATRIX_SECRET_KEY", raising=False)
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path}/nosecret.db",
            span_store="memory",
            secret_key=None,
        )
    )
    try:
        client = TestClient(app)
        _pid, roles = project_with_roles(app.state.session_factory, "NoSecret")
        mid = _create_model(client, roles["member"], "m1")["id"]
        resp = client.post(
            f"/api/v1/mlops/models/{mid}/webhooks",
            headers=roles["admin"],
            json={"url": "https://hooks.example.com/ci", "events": ["stage.changed"]},
        )
        assert resp.status_code == 503
        assert "MATRIX_SECRET_KEY" in resp.json()["detail"]
    finally:
        app.state.db_engine.dispose()


def _start_receiver(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[HTTPServer, int, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


def test_webhook_hmac_signature_allowlist_ssrf_and_secret_hygiene(env, caplog):
    client: TestClient = env["client"]
    admin_a = env["admin_a"]
    member_a = env["member_a"]
    viewer_a = env["viewer_a"]
    admin_b = env["admin_b"]
    factory = env["factory"]

    mid = _create_model(client, member_a, "webhook-model")["id"]
    _create_version(client, member_a, mid)

    # RBAC: member and viewer cannot create/list/delete webhooks
    assert (
        client.post(
            f"/api/v1/models/{mid}/webhooks",
            headers=member_a,
            json={"url": "http://hooks.example.com/ci", "events": ["stage.changed"]},
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/models/{mid}/webhooks", headers=member_a).status_code == 403
    assert client.get(f"/api/v1/models/{mid}/webhooks", headers=viewer_a).status_code == 403

    # Create webhook with server-generated secret
    created = client.post(
        f"/api/v1/models/{mid}/webhooks",
        headers=admin_a,
        json={"url": "http://hooks.example.com/ci", "events": ["stage.changed", "alias.moved"]},
    )
    assert created.status_code == 201, created.text
    wh = created.json()
    wh_id = wh["id"]
    secret = wh["secret"]
    assert secret.startswith("whsec_") and len(secret) > 20

    # Secret is encrypted at rest and NEVER returned on GET
    with factory() as db:
        row = db.get(ModelWebhook, wh_id)
        assert row is not None
        assert row.secret_encrypted != secret
        assert secret not in row.secret_encrypted
    listed = client.get(f"/api/v1/models/{mid}/webhooks", headers=admin_a)
    assert listed.status_code == 200
    assert "secret" not in listed.json()["items"][0]
    assert secret not in listed.text

    # Cross-project isolation
    assert client.get(f"/api/v1/models/{mid}/webhooks", headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/models/{mid}/webhooks/{wh_id}/deliveries", headers=admin_b).status_code == 404
    assert client.delete(f"/api/v1/models/{mid}/webhooks/{wh_id}", headers=admin_b).status_code == 404

    # 1) Host outside allowlist -> delivery fails cleanly with egress:host_not_allowed
    worker_runner.load_handler_modules()
    webhook_handlers.set_secret_key(SECRET_KEY)
    try:
        caplog.set_level(logging.DEBUG)
        assert (
            client.post(
                f"/api/v1/models/{mid}/versions/1/stage",
                headers=admin_a,
                json={"stage": "staging"},
            ).status_code
            == 200
        )
        assert worker_runner.run_once(factory, "test-worker") is True
        deliv_resp = client.get(f"/api/v1/models/{mid}/webhooks/{wh_id}/deliveries", headers=viewer_a)
        assert deliv_resp.status_code == 200
        d1 = deliv_resp.json()["items"][0]
        assert d1["status"] == "failed"
        assert d1["last_error"] == "egress:host_not_allowed"
        assert d1["attempts"] == 1
        assert secret not in deliv_resp.text

        # 2) Allowlist host + real HTTP receiver -> verify HMAC signature and no response body leak
        assert (
            client.post(
                f"/api/v1/projects/{env['pid_a']}/egress-hosts",
                headers=admin_a,
                json={"host": "hooks.example.com"},
            ).status_code
            == 201
        )

        received: list[dict[str, Any]] = []

        class _OkHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                received.append({"headers": dict(self.headers.items()), "body": body})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"TOP_SECRET_RECEIVER_RESPONSE_BODY")

            def log_message(self, format, *args):
                pass

        srv, port, _th = _start_receiver(_OkHandler)
        try:
            egress.set_resolver_for_tests(lambda host, port: [PUBLIC_IP])
            egress.set_dialer_for_tests(lambda ip, _p: ("127.0.0.1", port))

            assert (
                client.put(
                    f"/api/v1/models/{mid}/aliases/challenger",
                    headers=member_a,
                    json={"version": 1},
                ).status_code
                == 200
            )
            assert worker_runner.run_once(factory, "test-worker") is True
        finally:
            egress.set_resolver_for_tests(None)
            egress.set_dialer_for_tests(None)
            srv.shutdown()
            srv.server_close()

        assert len(received) == 1
        hdrs_lower = {k.lower(): v for k, v in received[0]["headers"].items()}
        body_bytes = received[0]["body"]
        ts = hdrs_lower["x-matrix-timestamp"]
        sig = hdrs_lower["x-matrix-signature"]
        expected_mac = hmac.new(
            secret.encode("utf-8"), f"{ts}.".encode() + body_bytes, hashlib.sha256
        ).hexdigest()
        assert sig == f"sha256={expected_mac}"
        assert hdrs_lower["x-matrix-event"] == "alias.moved"
        assert hdrs_lower["x-matrix-delivery"]
        parsed_body = json.loads(body_bytes.decode("utf-8"))
        assert parsed_body["alias"] == "challenger"
        assert parsed_body["version"] == 1

        deliv_resp2 = client.get(f"/api/v1/models/{mid}/webhooks/{wh_id}/deliveries", headers=viewer_a)
        assert deliv_resp2.status_code == 200
        d2 = deliv_resp2.json()["items"][0]
        assert d2["status"] == "delivered"
        assert d2["last_status_code"] == 200
        assert d2["delivered_at"] is not None
        assert "TOP_SECRET_RECEIVER_RESPONSE_BODY" not in deliv_resp2.text
        assert secret not in deliv_resp2.text
        assert secret not in caplog.text

        # 3) Redirect to private metadata IP -> blocked by egress_client
        class _RedirectSSRFHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                _ = self.rfile.read(length)
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()

            def log_message(self, format, *args):
                pass

        srv2, port2, _th2 = _start_receiver(_RedirectSSRFHandler)
        try:
            egress.set_resolver_for_tests(
                lambda host, port: ["169.254.169.254"] if host == "169.254.169.254" else [PUBLIC_IP]
            )
            egress.set_dialer_for_tests(lambda ip, _p: ("127.0.0.1", port2))
            assert (
                client.post(
                    f"/api/v1/models/{mid}/versions/1/stage",
                    headers=admin_a,
                    json={"stage": "production"},
                ).status_code
                == 200
            )
            assert worker_runner.run_once(factory, "test-worker") is True
        finally:
            egress.set_resolver_for_tests(None)
            egress.set_dialer_for_tests(None)
            srv2.shutdown()
            srv2.server_close()

        d3 = client.get(f"/api/v1/models/{mid}/webhooks/{wh_id}/deliveries", headers=viewer_a).json()["items"][0]
        assert d3["status"] == "failed"
        assert d3["last_error"] in (
            "egress:ip_literal_not_allowed",
            "egress:ip_blocked",
            "egress:link_local",
            "egress:host_not_allowed",
        )

        # 4) Transient 500 -> delivery stays queued and job is scheduled for retry with max_attempts=5
        class _Fail500Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                _ = self.rfile.read(length)
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"INTERNAL_ERROR_BODY")

            def log_message(self, format, *args):
                pass

        srv3, port3, _th3 = _start_receiver(_Fail500Handler)
        try:
            egress.set_resolver_for_tests(lambda host, port: [PUBLIC_IP])
            egress.set_dialer_for_tests(lambda ip, _p: ("127.0.0.1", port3))
            assert (
                client.put(
                    f"/api/v1/models/{mid}/aliases/canary",
                    headers=member_a,
                    json={"version": 1},
                ).status_code
                == 200
            )
            assert worker_runner.run_once(factory, "test-worker") is True
        finally:
            egress.set_resolver_for_tests(None)
            egress.set_dialer_for_tests(None)
            srv3.shutdown()
            srv3.server_close()

        d4 = client.get(f"/api/v1/models/{mid}/webhooks/{wh_id}/deliveries", headers=viewer_a).json()["items"][0]
        assert d4["status"] == "queued"
        assert d4["attempts"] == 1
        assert d4["last_status_code"] == 500
        assert "INTERNAL_ERROR_BODY" not in json.dumps(d4)
        with factory() as db:
            retry_job = db.scalars(
                select(Job).where(Job.kind == "webhooks.deliver").order_by(Job.created_at.desc())
            ).first()
            assert retry_job is not None
            assert retry_job.max_attempts == 5
            assert retry_job.status == "queued"

        # Delete webhook + verify audit
        del_wh = client.delete(f"/api/v1/models/{mid}/webhooks/{wh_id}", headers=admin_a)
        assert del_wh.status_code == 200
        with factory() as db:
            wh_audits = list(
                db.scalars(
                    select(AuditLog).where(
                        AuditLog.project_id == env["pid_a"],
                        AuditLog.action.in_(["model.webhook_create", "model.webhook_delete"]),
                    )
                )
            )
        assert {a.action for a in wh_audits} == {"model.webhook_create", "model.webhook_delete"}
        assert all(secret not in json.dumps(a.detail or {}) for a in wh_audits)
    finally:
        webhook_handlers.set_secret_key(None)


# ---------------------------------------------------------------------------
# 4. Model version files download
# ---------------------------------------------------------------------------


def test_model_version_files_download(env):
    client: TestClient = env["client"]
    member_a = env["member_a"]
    viewer_a = env["viewer_a"]
    admin_b = env["admin_b"]

    blob = b"lora-adapter-safetensors-bytes"
    sha = hashlib.sha256(blob).hexdigest()
    presign = client.post(
        "/api/v1/mlops/artifacts/presign-upload",
        headers=member_a,
        json={"name": "adapter.safetensors", "kind": "lora_adapter", "sha256": sha, "size_bytes": len(blob)},
    )
    assert presign.status_code == 201, presign.text
    art = presign.json()
    assert client.put(art["upload_url"], content=blob, headers=member_a).status_code == 204
    assert client.post(f"/api/v1/mlops/artifacts/{art['id']}/commit", headers=member_a).status_code == 200

    mid = _create_model(client, member_a, "files-model")["id"]
    _create_version(client, member_a, mid, artifact_id=art["id"])

    resp = client.get(f"/api/v1/models/{mid}/versions/1/files", headers=viewer_a)
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert "no-store" in resp.headers.get("cache-control", "")
    files = resp.json()["files"]
    assert len(files) == 1
    assert files[0]["sha256"] == sha
    assert files[0]["size"] == len(blob)
    dl_url = files[0]["url"]
    assert dl_url.startswith("/api/v1/artifacts/") and dl_url.endswith("/download")

    # Download works for viewer_a, blocked for other project
    dl = client.get(dl_url, headers=viewer_a)
    assert dl.status_code == 200 and dl.content == blob
    assert client.get(dl_url, headers=admin_b).status_code == 404
    assert client.get(f"/api/v1/models/{mid}/versions/1/files", headers=admin_b).status_code == 404


# ---------------------------------------------------------------------------
# 5. Bounded Lineage (direction, depth, <= 500 cap, cross-project isolation)
# ---------------------------------------------------------------------------


def test_bounded_lineage_direction_depth_and_500_cap(env):
    client: TestClient = env["client"]
    member_a = env["member_a"]
    viewer_a = env["viewer_a"]
    admin_b = env["admin_b"]
    pid_a = env["pid_a"]
    factory = env["factory"]

    # Build chain: dataset_commit -> run -> model_version -> experiment + benchmark
    ds = client.post("/api/v1/datasets", headers=member_a, json={"name": "lin-ds"}).json()
    c1 = client.post(
        f"/api/v1/datasets/{ds['id']}/commits",
        headers=member_a,
        json={"branch": "main", "message": "init", "changes": {"upsert": [{"item_key": "k1", "input": {"q": "hi"}}]}},
    ).json()
    run = client.post(
        "/api/v1/runs",
        headers=member_a,
        json={"name": "lin-run", "dataset_version_id": c1["id"]},
    ).json()
    with factory() as db:
        exp = Experiment(
            project_id=pid_a,
            name="lin-exp",
            dataset_id=ds["id"],
            variants=[{"name": "v1"}],
            metrics=[{"name": "acc"}],
            status="done",
        )
        db.add(exp)
        db.commit()
        exp_id = exp.id

    mid = _create_model(client, member_a, "lin-model")["id"]
    mv = _create_version(client, member_a, mid, run_id=run["id"], eval_experiment_id=exp_id)

    with factory() as db:
        bm = BenchmarkRun(
            project_id=pid_a,
            name="lin-bench",
            suite="lm_eval",
            tasks=["gsm8k"],
            target_model="lin-model",
            model_version_id=mv["id"],
            status="completed",
        )
        db.add(bm)
        db.commit()
        bm_id = bm.id

    # Query from run with direction=up -> sees dataset_commit, not model_version
    up = client.get(f"/api/v1/lineage?node=run:{run['id']}&direction=up&depth=2", headers=viewer_a)
    assert up.status_code == 200, up.text
    up_ids = {n["id"] for n in up.json()["nodes"]}
    assert f"dataset_commit:{c1['id']}" in up_ids
    assert f"model_version:{mv['id']}" not in up_ids

    # Query from run with direction=down & depth=1 -> sees model_version, not experiment/benchmark
    d1 = client.get(f"/api/v1/lineage?node=run:{run['id']}&direction=down&depth=1", headers=viewer_a)
    assert d1.status_code == 200
    d1_ids = {n["id"] for n in d1.json()["nodes"]}
    assert f"model_version:{mv['id']}" in d1_ids
    assert f"experiment:{exp_id}" not in d1_ids
    assert f"benchmark:{bm_id}" not in d1_ids

    # depth=2 reaches experiment and benchmark
    d2 = client.get(f"/api/v1/lineage?node=run:{run['id']}&direction=down&depth=2", headers=viewer_a)
    assert d2.status_code == 200
    d2_ids = {n["id"] for n in d2.json()["nodes"]}
    assert f"experiment:{exp_id}" in d2_ids
    assert f"benchmark:{bm_id}" in d2_ids

    # data_version alias works for dataset_commit
    dv = client.get(f"/api/v1/lineage?node=data_version:{c1['id']}&direction=down&depth=2", headers=viewer_a)
    assert dv.status_code == 200
    assert f"run:{run['id']}" in {n["id"] for n in dv.json()["nodes"]}

    # Invalid node syntax / kind -> 422; cross-project -> 404
    assert client.get("/api/v1/lineage?node=invalid", headers=viewer_a).status_code == 422
    assert client.get("/api/v1/lineage?node=unknown_kind:123", headers=viewer_a).status_code == 422
    assert client.get(f"/api/v1/lineage?node=run:{run['id']}", headers=admin_b).status_code == 404

    # 500-node cap + truncated=True
    with factory() as db:
        for i in range(520):
            db.add(
                ArtifactLineage(
                    project_id=pid_a,
                    source_type="run",
                    source_id=run["id"],
                    target_type="artifact",
                    target_id=f"art-{i:04d}",
                    relation="produced",
                    metadata_={},
                )
            )
        db.commit()

    capped = client.get(f"/api/v1/lineage?node=run:{run['id']}&direction=down&depth=1", headers=viewer_a)
    assert capped.status_code == 200, capped.text
    cdata = capped.json()
    assert len(cdata["nodes"]) == 500
    assert cdata["truncated"] is True


# ---------------------------------------------------------------------------
# 6. Datasets commit cap & runs.dataset_version_id validation
# ---------------------------------------------------------------------------


def test_dataset_commit_item_cap_and_run_dataset_validation(env):
    client: TestClient = env["client"]
    member_a = env["member_a"]
    member_b = env["member_b"]

    ds_a = client.post("/api/v1/datasets", headers=member_a, json={"name": "capped-ds"}).json()
    ds_b = client.post("/api/v1/datasets", headers=member_b, json={"name": "other-ds"}).json()

    # Cap is 5 in fixture: 5 items succeed, 6 items fail with 422
    items_5 = [{"item_key": f"k{i}", "input": {"i": i}} for i in range(5)]
    ok = client.post(
        f"/api/v1/datasets/{ds_a['id']}/commits",
        headers=member_a,
        json={"branch": "main", "message": "five items", "changes": {"upsert": items_5}},
    )
    assert ok.status_code == 201, ok.text
    commit_a = ok.json()["id"]

    too_many = client.post(
        f"/api/v1/datasets/{ds_a['id']}/commits",
        headers=member_a,
        json={
            "branch": "main",
            "message": "sixth item exceeds cap",
            "changes": {"upsert": [{"item_key": "k5", "input": {"i": 5}}]},
        },
    )
    assert too_many.status_code == 422

    # Commit in project B
    commit_b = client.post(
        f"/api/v1/datasets/{ds_b['id']}/commits",
        headers=member_b,
        json={"branch": "main", "message": "b", "changes": {"upsert": [{"item_key": "b1", "input": {"x": 1}}]}},
    ).json()["id"]

    # Run with valid commit in project A -> 201
    r_ok = client.post("/api/v1/runs", headers=member_a, json={"dataset_version_id": commit_a})
    assert r_ok.status_code == 201, r_ok.text
    assert r_ok.json()["dataset_version_id"] == commit_a

    # Run with commit from project B -> 422 (cross-project blocked)
    assert client.post("/api/v1/runs", headers=member_a, json={"dataset_version_id": commit_b}).status_code == 422
    # Run with nonexistent 32-hex commit ID -> 422
    assert (
        client.post("/api/v1/runs", headers=member_a, json={"dataset_version_id": "a" * 32}).status_code == 422
    )


# ---------------------------------------------------------------------------
# 7. Migration fragment `server/migrations/fragments/t6_registry.py`
# ---------------------------------------------------------------------------


def test_t6_migration_fragment_upgrade_and_downgrade(tmp_path):
    fragment_path = Path(__file__).resolve().parents[1] / "migrations" / "fragments" / "t6_registry.py"
    spec = importlib.util.spec_from_file_location("t6_registry_fragment", fragment_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    eng = make_engine(f"sqlite:///{tmp_path}/frag.db")
    try:
        # Simulate pre-T6 (0008) schema: create all tables except the 3 new T6 tables and T6 partial index.
        t6_tables = {"model_stage_history", "model_webhooks", "webhook_deliveries"}
        pre_tables = [t for t in Base.metadata.sorted_tables if t.name not in t6_tables]
        Base.metadata.create_all(eng, tables=pre_tables)
        with eng.begin() as conn:
            conn.execute(sa.text("DROP INDEX IF EXISTS uq_mlops_model_versions_one_production"))
            # Seed a model with 2 duplicate production versions to test deduplication during upgrade
            conn.execute(
                sa.insert(RegisteredModel.__table__).values(
                    id="m1", project_id="p1", name="dup-prod", model_type="llm", tags=[]
                )
            )
            conn.execute(
                sa.insert(ModelVersion.__table__).values(
                    id="mv1", project_id="p1", model_id="m1", version=1, stage="production", metrics_snapshot={}
                )
            )
            conn.execute(
                sa.insert(ModelVersion.__table__).values(
                    id="mv2", project_id="p1", model_id="m1", version=2, stage="production", metrics_snapshot={}
                )
            )

        with eng.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()

        insp = sa.inspect(eng)
        assert t6_tables.issubset(set(insp.get_table_names()))
        mv_indexes = {idx["name"] for idx in insp.get_indexes("mlops_model_versions")}
        assert "uq_mlops_model_versions_one_production" in mv_indexes
        assert "ix_mlops_mv_project_eval_exp" in mv_indexes
        runs_indexes = {idx["name"] for idx in insp.get_indexes("runs")}
        assert "ix_runs_project_dataset_version" in runs_indexes

        # Older duplicate production version (v1) was archived, v2 kept as production
        with eng.connect() as conn:
            rows = dict(
                conn.execute(sa.text("SELECT version, stage FROM mlops_model_versions WHERE model_id = 'm1'")).fetchall()
            )
            assert rows == {1: "archived", 2: "production"}

        # Downgrade removes T6 tables and indexes cleanly
        with eng.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.downgrade()

        insp2 = sa.inspect(eng)
        assert t6_tables.isdisjoint(set(insp2.get_table_names()))
        mv_indexes2 = {idx["name"] for idx in insp2.get_indexes("mlops_model_versions")}
        assert "uq_mlops_model_versions_one_production" not in mv_indexes2
    finally:
        eng.dispose()
