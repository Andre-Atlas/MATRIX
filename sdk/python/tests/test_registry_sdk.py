"""Tests for canonical RegistryClient, WebhooksClient, bounded lineage, deprecation, proxy/TLS, and examples."""

from __future__ import annotations

import hashlib
import json
import runpy
import ssl
import warnings
from pathlib import Path
from typing import Any

import httpx
import pytest

import matrix_sdk as mx
from matrix_sdk import RegistryClient, WebhooksClient, _net, tracking


def _make_client(handler: Any) -> mx.MatrixClient:
    return mx.MatrixClient(
        api_key="mx_test",
        endpoint="https://matrix.local",
        transport=httpx.MockTransport(handler),
    )


def test_canonical_registry_register_promote_alias_history_and_delete() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, request.url.path, body, str(request.url.query.decode())))
        p = request.url.path
        m = request.method
        if m == "POST" and p == "/api/v1/mlops/models":
            return httpx.Response(201, json={"id": "mod_1", "name": body["name"], "model_type": body["model_type"]})
        if m == "GET" and p == "/api/v1/mlops/models":
            return httpx.Response(200, json={"items": [{"id": "mod_1", "name": "fraud-clf"}]})
        if m == "GET" and p == "/api/v1/mlops/models/mod_1":
            return httpx.Response(200, json={"id": "mod_1", "name": "fraud-clf", "versions": [], "aliases": {}})
        if m == "POST" and p == "/api/v1/mlops/models/mod_1/versions":
            return httpx.Response(201, json={"id": "mv_1", "model_id": "mod_1", "version": 1, "stage": body["stage"]})
        if m == "POST" and p == "/api/v1/models/mod_1/versions/1/stage":
            return httpx.Response(200, json={"id": "mv_1", "model_id": "mod_1", "version": 1, "stage": body["stage"]})
        if m == "PUT" and p == "/api/v1/models/mod_1/aliases/champion":
            return httpx.Response(200, json={"model_id": "mod_1", "alias": "champion", "version": body["version"]})
        if m == "DELETE" and p == "/api/v1/models/mod_1/aliases/champion":
            return httpx.Response(200, json={"deleted": True, "model_id": "mod_1", "alias": "champion"})
        if m == "GET" and p == "/api/v1/models/mod_1/history":
            return httpx.Response(
                200,
                json={
                    "model_id": "mod_1",
                    "items": [{"version": 1, "from_stage": "none", "to_stage": "production", "comment": "ship it"}],
                },
            )
        if m == "DELETE" and p == "/api/v1/mlops/models/mod_1":
            return httpx.Response(200, json={"deleted": True, "id": "mod_1"})
        raise AssertionError(f"Unexpected request: {m} {request.url}")

    client = _make_client(handler)
    try:
        assert isinstance(client.registry, RegistryClient)
        assert isinstance(client.registry.webhooks, WebhooksClient)

        model = mx.register("fraud-clf", model_type="sklearn", client=client)
        assert model["id"] == "mod_1"
        assert len(client.registry.list()["items"]) == 1
        assert client.registry.get("mod_1")["name"] == "fraud-clf"

        mv = client.registry.create_version("mod_1", run_id="run_1", metrics_snapshot={"f1": 0.92})
        assert mv["version"] == 1

        promoted = mx.promote("mod_1", 1, "production", comment="ship it", client=client)
        assert promoted["stage"] == "production"

        alias_set = mx.set_alias("mod_1", "@champion", 1, client=client)
        assert alias_set == {"model_id": "mod_1", "alias": "champion", "version": 1}

        hist = client.registry.history("mod_1", version=1, limit=25)
        assert hist["items"][0]["to_stage"] == "production"

        deleted_alias = client.registry.delete_alias("mod_1", "@champion")
        assert deleted_alias["deleted"] is True

        deleted_model = client.registry.delete("mod_1", force=True)
        assert deleted_model["deleted"] is True

        # Verify canonical T6 paths were hit
        paths = [(m, p) for m, p, _, _ in calls]
        assert ("POST", "/api/v1/models/mod_1/versions/1/stage") in paths
        assert ("PUT", "/api/v1/models/mod_1/aliases/champion") in paths
        assert ("DELETE", "/api/v1/models/mod_1/aliases/champion") in paths
        assert ("GET", "/api/v1/models/mod_1/history") in paths
    finally:
        client._http.close()


def test_log_model_uploads_artifact_links_run_and_handles_existing_model(tmp_path: Path) -> None:
    model_file = tmp_path / "weights.safetensors"
    payload = b"weights-payload-bytes" * 64
    model_file.write_bytes(payload)
    expected_sha = hashlib.sha256(payload).hexdigest()

    model_exists = {"created": False}
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        m, p = request.method, request.url.path
        if m == "POST" and p == "/api/v1/artifacts":
            raw = request.read()
            assert request.headers["x-sha256"] == expected_sha
            assert request.headers["x-owner-kind"] == "run"
            assert request.headers["x-owner-id"] == "run_42"
            return httpx.Response(201, json={"id": "blob_1", "name": "weights.safetensors", "size": len(raw), "sha256": expected_sha})
        if m == "POST" and p == "/api/v1/runs/run_42/artifacts":
            body = json.loads(request.content.decode())
            assert body == {"artifact_id": "blob_1", "name": "llama-lora", "kind": "model"}
            return httpx.Response(201, json=body)
        if m == "POST" and p == "/api/v1/mlops/models":
            if model_exists["created"]:
                return httpx.Response(409, json={"detail": "model name already exists"})
            model_exists["created"] = True
            return httpx.Response(201, json={"id": "mod_42", "name": "llama-lora", "model_type": "lora"})
        if m == "GET" and p == "/api/v1/mlops/models":
            return httpx.Response(200, json={"items": [{"id": "mod_42", "name": "llama-lora", "model_type": "lora"}]})
        if m == "POST" and p == "/api/v1/mlops/models/mod_42/versions":
            body = json.loads(request.content.decode())
            assert body["run_id"] == "run_42"
            assert "artifact_id" not in body
            ver = 2 if seen.count(("POST", "/api/v1/mlops/models/mod_42/versions")) > 1 else 1
            return httpx.Response(201, json={"id": f"mv_{ver}", "model_id": "mod_42", "version": ver, "stage": body["stage"]})
        raise AssertionError(f"Unexpected request: {m} {request.url}")

    client = _make_client(handler)
    try:
        res1 = mx.log_model(model_file, "llama-lora", model_type="lora", run_id="run_42", client=client)
        assert res1["model_id"] == "mod_42"
        assert res1["version"] == 1
        assert res1["artifact"]["id"] == "blob_1"

        # Second call hits 409 on register, looks up existing model, creates v2
        res2 = client.registry.log_model(model_file, "llama-lora", model_type="lora", run_id="run_42")
        assert res2["model_id"] == "mod_42"
        assert res2["version"] == 2
    finally:
        client._http.close()


def test_webhooks_crud_and_deliveries() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, request.url.path, body))
        m, p = request.method, request.url.path
        if m == "POST" and p == "/api/v1/models/mod_1/webhooks":
            return httpx.Response(
                201,
                json={"id": "wh_1", "model_id": "mod_1", "url": body["url"], "events": body["events"], "secret": "whsec_once"},
            )
        if m == "GET" and p == "/api/v1/models/mod_1/webhooks":
            return httpx.Response(200, json={"model_id": "mod_1", "items": [{"id": "wh_1", "url": "https://ci.example/hook"}]})
        if m == "GET" and p == "/api/v1/models/mod_1/webhooks/wh_1/deliveries":
            return httpx.Response(200, json={"webhook_id": "wh_1", "items": [{"id": "del_1", "status": "delivered"}]})
        if m == "DELETE" and p == "/api/v1/models/mod_1/webhooks/wh_1":
            return httpx.Response(200, json={"deleted": True, "id": "wh_1"})
        raise AssertionError(f"Unexpected request: {m} {request.url}")

    client = _make_client(handler)
    try:
        wh = client.registry.webhooks.create("mod_1", "https://ci.example/hook", ["stage.changed"])
        assert wh["id"] == "wh_1"
        assert wh["secret"] == "whsec_once"

        listed = client.registry.list_webhooks("mod_1")
        assert len(listed["items"]) == 1

        deliveries = client.registry.webhooks.deliveries("mod_1", "wh_1", limit=10)
        assert deliveries["items"][0]["status"] == "delivered"

        deleted = client.registry.delete_webhook("mod_1", "wh_1")
        assert deleted["deleted"] is True
    finally:
        client._http.close()


def test_bounded_lineage_query() -> None:
    seen_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v1/lineage":
            seen_queries.append(dict(request.url.params))
            return httpx.Response(
                200,
                json={
                    "root": request.url.params["node"],
                    "direction": request.url.params["direction"],
                    "depth": int(request.url.params["depth"]),
                    "truncated": False,
                    "nodes": [{"id": "run:r1", "type": "run"}, {"id": "model_version:mv1", "type": "model_version"}],
                    "edges": [{"source": "run:r1", "target": "model_version:mv1", "relation": "registered_as"}],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    try:
        graph = mx.lineage("run:r1", direction="down", depth=2, max_nodes=50, client=client)
        assert graph["root"] == "run:r1"
        assert graph["truncated"] is False
        assert len(graph["nodes"]) == 2

        graph2 = client.lineage("model_version:mv1")
        assert graph2["direction"] == "both"
        assert graph2["depth"] == 3

        assert seen_queries[0] == {"node": "run:r1", "direction": "down", "depth": "2", "max_nodes": "50"}
        assert seen_queries[1] == {"node": "model_version:mv1", "direction": "both", "depth": "3"}
    finally:
        client._http.close()


def test_mlops_shim_emits_deprecation_warnings_and_never_calls_deprecated_routes(tmp_path: Path) -> None:
    called_paths: list[str] = []
    payload = b"model-bytes"
    sha = hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        called_paths.append(p)
        m = request.method
        if m == "POST" and p == "/api/v1/runs":
            return httpx.Response(201, json={"id": "r_dep", "name": "dep", "status": "running"})
        if m == "PATCH" and p == "/api/v1/runs/r_dep":
            return httpx.Response(200, json={"id": "r_dep", "status": "finished"})
        if m == "POST" and p == "/api/v1/sweeps":
            return httpx.Response(201, json={"id": "sw_dep", "name": "dep-sw"})
        if m == "GET" and p == "/api/v1/sweeps/sw_dep":
            return httpx.Response(200, json={"sweep": {"id": "sw_dep", "config": {"metric": {"key": "loss"}}}})
        if m == "POST" and p == "/api/v1/sweeps/sw_dep/next":
            return httpx.Response(204)
        if m == "POST" and p == "/api/v1/mlops/models":
            return httpx.Response(201, json={"id": "m_dep", "name": "m"})
        if m == "POST" and p == "/api/v1/mlops/models/m_dep/versions":
            return httpx.Response(201, json={"id": "mv_dep", "version": 1})
        if m == "PUT" and p == "/api/v1/models/m_dep/aliases/production":
            return httpx.Response(200, json={"model_id": "m_dep", "alias": "production", "version": 1})
        if m == "GET" and p == "/api/v1/mlops/models/m_dep":
            return httpx.Response(200, json={"id": "m_dep", "name": "m", "aliases": {"production": 1}, "versions": []})
        if m == "GET" and p == "/api/v1/models/m_dep/versions/1/files":
            return httpx.Response(
                200,
                json={"model_id": "m_dep", "version": 1, "files": [{"url": "/api/v1/artifacts/b1/download", "sha256": sha}]},
            )
        if m == "GET" and p == "/api/v1/artifacts/b1/download":
            return httpx.Response(200, content=payload)
        raise AssertionError(f"Unexpected request: {m} {request.url}")

    client = _make_client(handler)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with client.mlops.start_run("dep", flush_interval=60.0):
                pass
            client.mlops.create_sweep("dep-sw", search_space={"lr": {"min": 1e-4, "max": 1e-2}})
            client.mlops.run_sweep_agent("sw_dep", lambda r, hp: None)
            client.mlops.register_model("m")
            client.mlops.create_model_version("m_dep", artifact_id="a1")
            client.mlops.set_model_alias("m_dep", "production", 1)
            client.mlops.download_model("m_dep", alias="production", dest_dir=tmp_path)

        dep_msgs = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
        for expected in (
            "start_run",
            "create_sweep",
            "run_sweep_agent",
            "register_model",
            "create_model_version",
            "set_model_alias",
            "download_model",
        ):
            assert any(expected in msg for msg in dep_msgs), f"Missing DeprecationWarning for {expected}"

        # Assert NO deprecated /api/v1/mlops/runs*, /artifacts*, or /sweeps* routes were ever called
        forbidden_prefixes = ("/api/v1/mlops/runs", "/api/v1/mlops/artifacts", "/api/v1/mlops/sweeps")
        assert not any(p.startswith(forbidden_prefixes) for p in called_paths), f"Called deprecated path: {called_paths}"
    finally:
        client._http.close()


def test_proxy_and_tls_client_kwargs_honour_env_and_never_disable_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.internal:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:8443")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,[::1],.internal.local")

    kwargs = _net.client_kwargs()
    assert kwargs["trust_env"] is False
    assert isinstance(kwargs["verify"], ssl.SSLContext)
    assert kwargs["verify"].verify_mode != ssl.CERT_NONE
    mounts = kwargs["mounts"]
    assert mounts is not None
    assert "http://" in mounts
    assert "https://" in mounts
    assert mounts.get("all://localhost") is None
    assert mounts.get("all://[::1]") is None
    assert mounts.get("all://*.internal.local") is None


def test_examples_train_sklearn_and_train_pytorch_run_cleanly(client: mx.MatrixClient, server: Any) -> None:
    examples_dir = Path(__file__).resolve().parents[1] / "examples"
    prev_default = mx.client._default_client
    mx.client._default_client = client
    try:
        runpy.run_path(str(examples_dir / "train_sklearn.py"), run_name="__main__")
        runpy.run_path(str(examples_dir / "train_pytorch.py"), run_name="__main__")
    finally:
        mx.client._default_client = prev_default
        tracking._current = None

    run_creates = server.calls("POST", "/api/v1/runs")
    assert len(run_creates) == 2
    names = {body["name"] for *_, body in run_creates}
    assert names == {"iris-rf", "toy-regression"}
    assert len(server.calls("POST", "/api/v1/runs/r1/reports")) >= 1
    assert len(server.calls("POST", "/api/v1/artifacts")) >= 1
    assert len(server.points()) > 0
