"""Tests for matrix_sdk.mlops deprecated compatibility layer (TrainingRun, MLOpsClient, MatrixTrainerCallback)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from matrix_sdk import MatrixClient, MatrixError, MatrixTrainerCallback, MLOpsClient, TrainingRun


def _make_client(handler: Any) -> MatrixClient:
    return MatrixClient(
        api_key="test-key",
        endpoint="https://matrix.local",
        transport=httpx.MockTransport(handler),
    )


def test_training_run_context_manager_logs_flushes_and_completes() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(201, json={"id": "run_1", "name": "lora-sft", "status": "running"})
        if request.method == "POST" and request.url.path == "/api/v1/runs/run_1/metrics":
            return httpx.Response(202, json={"accepted": len(body["points"]), "rejected": []})
        if request.method == "PATCH" and request.url.path == "/api/v1/runs/run_1":
            return httpx.Response(200, json={"id": "run_1", "status": body["status"]})
        if request.method == "POST" and request.url.path == "/api/v1/runs/run_1/heartbeat":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    assert isinstance(client.mlops, MLOpsClient)

    with pytest.warns(DeprecationWarning, match="start_run"):
        run = client.mlops.start_run("lora-sft", run_type="lora", hyperparameters={"lr": 2e-4}, flush_interval=60.0)
    with run:
        assert isinstance(run, TrainingRun)
        run.log({"loss": 1.25, "lr": 2e-4}, step=1, epoch=0.1)
        run.log({"loss": 0.95}, step=2, epoch=0.2)

    assert calls[0][0:2] == ("POST", "/api/v1/runs")
    assert calls[0][2]["params"] == {"lr": 2e-4}
    metric_calls = [c for c in calls if c[1] == "/api/v1/runs/run_1/metrics"]
    assert len(metric_calls) >= 1
    all_points = [pt for c in metric_calls for pt in c[2]["points"]]
    assert {p["key"] for p in all_points} == {"loss", "lr", "epoch"}
    assert calls[-1][0:2] == ("PATCH", "/api/v1/runs/run_1")
    assert calls[-1][2]["status"] == "finished"


def test_training_run_marks_failed_on_exception_and_rejects_non_finite() -> None:
    patch_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(201, json={"id": "run_fail", "name": "fail-test", "status": "running"})
        if request.method == "POST" and request.url.path == "/api/v1/runs/run_fail/metrics":
            return httpx.Response(202, json={"accepted": len(body["points"]), "rejected": []})
        if request.method == "PATCH" and request.url.path == "/api/v1/runs/run_fail":
            patch_bodies.append(body)
            return httpx.Response(200, json={"id": "run_fail", "status": body["status"]})
        if request.method == "POST" and request.url.path == "/api/v1/runs/run_fail/heartbeat":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)

    with pytest.warns(DeprecationWarning, match="start_run"):
        run = client.mlops.start_run("fail-test", flush_interval=60.0)
    with pytest.raises(RuntimeError, match="oom"), run:
        with pytest.raises(ValueError, match="finite"):
            run.log({"loss": float("nan")}, step=1)
        with pytest.raises(ValueError, match="finite"):
            run.log({"loss": float("inf")}, step=1)
        run.log({"loss": 2.0}, step=1)
        raise RuntimeError("oom")

    assert len(patch_bodies) == 1
    assert patch_bodies[0]["status"] == "failed"
    assert "RuntimeError: oom" in patch_bodies[0]["error"]


def test_log_artifact_upload_via_canonical_store(tmp_path: Path) -> None:
    adapter_file = tmp_path / "adapter_model.safetensors"
    payload = b"safetensors-binary-payload-" * 1024
    adapter_file.write_bytes(payload)
    expected_sha = hashlib.sha256(payload).hexdigest()

    uploaded_blobs: list[tuple[str, bytes, dict[str, str]]] = []
    linked: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(201, json={"id": "run_art", "name": "art-run", "status": "running"})
        if request.method == "PATCH" and request.url.path == "/api/v1/runs/run_art":
            return httpx.Response(200, json={"id": "run_art", "status": "finished"})
        if request.method == "POST" and request.url.path == "/api/v1/artifacts":
            raw = request.read()
            art_id = f"art_{len(uploaded_blobs) + 1}"
            uploaded_blobs.append((art_id, raw, dict(request.headers)))
            return httpx.Response(
                201,
                json={
                    "id": art_id,
                    "name": request.headers["x-filename"],
                    "sha256": request.headers["x-sha256"],
                    "size": len(raw),
                    "status": "ready",
                },
            )
        if request.method == "POST" and request.url.path == "/api/v1/runs/run_art/artifacts":
            body = json.loads(request.content.decode())
            linked.append(body)
            return httpx.Response(201, json=body)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with pytest.warns(DeprecationWarning, match="start_run"):
        run = client.mlops.start_run("art-run", flush_interval=60.0)
    with run:
        art1 = run.log_artifact(adapter_file, name="lora-v1", kind="lora_adapter", format="safetensors")
        assert art1["id"] == "art_1"
        assert art1["sha256"] == expected_sha
        assert uploaded_blobs[0][1] == payload
        assert linked[0] == {"artifact_id": "art_1", "name": "lora-v1", "kind": "model"}

    # Rejects directory and symlink
    symlink_path = tmp_path / "link.safetensors"
    symlink_path.symlink_to(adapter_file)
    with pytest.raises(ValueError, match="symlink"):
        run.log_artifact(symlink_path, name="bad")
    with pytest.raises(ValueError, match="regular file"):
        run.log_artifact(tmp_path, name="bad-dir")


def test_run_sweep_agent_runs_trials_and_stops_on_204() -> None:
    trial_counter = {"n": 0}
    seen_hyperparams: list[dict[str, Any]] = []
    finished_runs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v1/sweeps/sw_1":
            return httpx.Response(
                200,
                json={"sweep": {"id": "sw_1", "config": {"metric": {"key": "eval/loss"}}}},
            )
        if request.method == "POST" and request.url.path == "/api/v1/sweeps/sw_1/next":
            trial_counter["n"] += 1
            if trial_counter["n"] <= 2:
                hp = {"lr": 1e-4 * trial_counter["n"]}
                return httpx.Response(
                    200,
                    json={
                        "sweep_id": "sw_1",
                        "trial_index": trial_counter["n"] - 1,
                        "params": hp,
                        "run": {"id": f"run_t{trial_counter['n']}", "name": f"trial-{trial_counter['n']}", "status": "running"},
                    },
                )
            return httpx.Response(204)
        if request.method == "POST" and request.url.path.endswith("/report"):
            return httpx.Response(200, json={"action": "continue"})
        if request.method == "POST" and request.url.path.endswith("/metrics"):
            body = json.loads(request.content.decode())
            return httpx.Response(202, json={"accepted": len(body["points"]), "rejected": []})
        if request.method == "PATCH" and request.url.path.startswith("/api/v1/runs/"):
            finished_runs.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"status": "finished"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)

    def train_fn(run: TrainingRun, hp: dict[str, Any]) -> None:
        seen_hyperparams.append(hp)
        run.log({"eval/loss": 0.5 / (hp["lr"] * 1e4)}, step=1)

    with pytest.warns(DeprecationWarning, match="run_sweep_agent"):
        client.mlops.run_sweep_agent("sw_1", train_fn)

    assert len(seen_hyperparams) == 2
    assert seen_hyperparams == [{"lr": 1e-4}, {"lr": 2e-4}]
    assert finished_runs == ["run_t1", "run_t2"]


def test_model_registry_and_download_sha256_validation(tmp_path: Path) -> None:
    model_bytes = b"model-weights-v1" * 100
    good_sha = hashlib.sha256(model_bytes).hexdigest()
    bad_sha = "0" * 64
    serve_bad_sha = {"bad": False}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        if request.method == "POST" and request.url.path == "/api/v1/mlops/models":
            assert body["model_type"] == "lora"
            return httpx.Response(201, json={"id": "mod_1", "name": body["name"], "model_type": body["model_type"]})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/models/mod_1/versions":
            return httpx.Response(201, json={"id": "mv_1", "model_id": "mod_1", "version": 1, "artifact_id": body["artifact_id"]})
        if request.method == "PUT" and request.url.path == "/api/v1/models/mod_1/aliases/production":
            return httpx.Response(200, json={"model_id": "mod_1", "alias": "production", "version": body["version"]})
        if request.method == "GET" and request.url.path == "/api/v1/mlops/models/mod_1":
            return httpx.Response(
                200,
                json={
                    "id": "mod_1",
                    "name": "llama-lora",
                    "versions": [{"id": "mv_1", "version": 1, "artifact_id": "art_dl"}],
                    "aliases": {"production": 1},
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/models/mod_1/versions/1/files":
            return httpx.Response(
                200,
                json={
                    "model_id": "mod_1",
                    "version": 1,
                    "files": [
                        {
                            "artifact_id": "blob_1",
                            "path": "weights.bin",
                            "url": "/api/v1/artifacts/blob_1/download",
                            "sha256": bad_sha if serve_bad_sha["bad"] else good_sha,
                            "size": len(model_bytes),
                        }
                    ],
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/artifacts/blob_1/download":
            return httpx.Response(200, content=model_bytes)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    model = client.register_model("llama-lora", task_type="lora", base_model="meta-llama/Llama-3-8B")
    assert model["id"] == "mod_1"

    with pytest.warns(DeprecationWarning, match="create_model_version"):
        mv = client.mlops.create_model_version("mod_1", artifact_id="art_dl", run_id="run_1", lora_config={"r": 16})
    assert mv["version"] == 1

    with pytest.warns(DeprecationWarning, match="set_model_alias"):
        alias_resp = client.mlops.set_model_alias("mod_1", "production", 1)
    assert alias_resp["alias"] == "production"

    with pytest.warns(DeprecationWarning, match="download_model"):
        downloaded = client.mlops.download_model("mod_1", alias="production", dest_dir=tmp_path)
    assert downloaded.exists()
    assert downloaded.read_bytes() == model_bytes

    serve_bad_sha["bad"] = True
    with (
        pytest.warns(DeprecationWarning, match="download_model"),
        pytest.raises(MatrixError, match="SHA-256 mismatch"),
    ):
        client.mlops.download_model("mod_1", alias="production", dest_dir=tmp_path / "corrupt")


def test_matrix_trainer_callback_logs_and_propagates_early_stop() -> None:
    metric_batches: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(201, json={"id": "run_cb", "name": "cb-run", "status": "running", "sweep_id": "sw_cb"})
        if request.method == "POST" and request.url.path == "/api/v1/runs/run_cb/metrics":
            metric_batches.append(body)
            return httpx.Response(202, json={"accepted": len(body["points"]), "rejected": []})
        if request.method == "POST" and request.url.path == "/api/v1/sweeps/sw_cb/runs/run_cb/report":
            return httpx.Response(200, json={"action": "stop"})
        if request.method == "PATCH" and request.url.path == "/api/v1/runs/run_cb":
            return httpx.Response(200, json={"id": "run_cb", "status": body["status"]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with pytest.warns(DeprecationWarning, match="start_run"):
        run = client.mlops.start_run("cb-run", sweep_id="sw_cb", flush_interval=60.0)
    with run:
        run._run._sweep_metric_key = "eval_loss"
        cb = MatrixTrainerCallback(run)
        state = SimpleNamespace(global_step=10, epoch=1.0)
        control = SimpleNamespace(should_training_stop=False)
        args = SimpleNamespace()

        cb.on_log(args, state, control, logs={"loss": 0.42, "eval_loss": 0.51, "note": "ignored"})
        run.flush()
        assert run.should_stop is True
        assert control.should_training_stop is True

        cb.on_step_end(args, state, control)
        assert control.should_training_stop is True

        cb.on_evaluate(args, state, control, metrics={"eval_accuracy": 0.91})
        cb.on_train_end(args, state, control)

    points = [p for b in metric_batches for p in b["points"]]
    keys = {p["key"] for p in points}
    assert {"loss", "eval_loss", "eval_accuracy", "epoch"}.issubset(keys)


def test_protocol_relative_url_rejected_without_forwarding_auth(tmp_path: Path) -> None:
    payload = b"weights-data"
    good_sha = hashlib.sha256(payload).hexdigest()
    evil_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "evil.example":
            evil_requests.append(request)
            return httpx.Response(200)
        if request.method == "GET" and request.url.path == "/api/v1/mlops/models/mod_sec":
            return httpx.Response(
                200,
                json={
                    "id": "mod_sec",
                    "name": "sec-model",
                    "versions": [{"id": "mv_1", "version": 1, "artifact_id": "art_evil"}],
                    "aliases": {"production": 1},
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/models/mod_sec/versions/1/files":
            return httpx.Response(
                200,
                json={
                    "model_id": "mod_sec",
                    "version": 1,
                    "files": [
                        {
                            "artifact_id": "blob_evil",
                            "path": "weights.bin",
                            "url": "//evil.example/leak-download",
                            "sha256": good_sha,
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with (
        pytest.warns(DeprecationWarning, match="download_model"),
        pytest.raises(ValueError, match="Unsupported external URL scheme"),
    ):
        client.mlops.download_model("mod_sec", alias="production", dest_dir=tmp_path)

    assert evil_requests == []


def test_external_presigned_get_omits_authorization_and_uses_proxy_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"presigned-s3-payload-" * 16
    expected_sha = hashlib.sha256(payload).hexdigest()

    ext_requests: list[tuple[str, str, dict[str, str]]] = []
    client_init_kwargs: list[dict[str, Any]] = []
    real_httpx_client = httpx.Client

    def ext_handler(request: httpx.Request) -> httpx.Response:
        ext_requests.append((request.method, str(request.url), dict(request.headers)))
        if request.method == "GET":
            return httpx.Response(200, content=payload)
        raise AssertionError(f"Unexpected external request: {request.method} {request.url}")

    def patched_client(*args: Any, **kwargs: Any) -> httpx.Client:
        if "transport" not in kwargs:
            client_init_kwargs.append(dict(kwargs))
            # Swap out mounts/verify for mock transport while recording what was passed
            kwargs.pop("mounts", None)
            kwargs.pop("verify", None)
            kwargs["transport"] = httpx.MockTransport(ext_handler)
        return real_httpx_client(*args, **kwargs)

    monkeypatch.setattr("matrix_sdk.registry.httpx.Client", patched_client)

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer test-key"
        if request.method == "GET" and request.url.path == "/api/v1/mlops/models/mod_s3":
            return httpx.Response(
                200,
                json={
                    "id": "mod_s3",
                    "name": "s3-model",
                    "versions": [{"id": "mv_1", "version": 1, "artifact_id": "art_s3"}],
                    "aliases": {"production": 1},
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/models/mod_s3/versions/1/files":
            return httpx.Response(
                200,
                json={
                    "model_id": "mod_s3",
                    "version": 1,
                    "files": [
                        {
                            "artifact_id": "blob_s3",
                            "path": "model.bin",
                            "url": "https://s3.example.com/bucket/art_s3?X-Amz-Signature=def",
                            "sha256": expected_sha,
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected API request: {request.method} {request.url}")

    client = _make_client(api_handler)
    with pytest.warns(DeprecationWarning, match="download_model"):
        dl_path = client.mlops.download_model("mod_s3", alias="production", dest_dir=tmp_path / "dl")
    assert dl_path.read_bytes() == payload

    assert len(ext_requests) == 1
    assert ext_requests[0][0] == "GET"
    assert "authorization" not in {k.lower() for k in ext_requests[0][2]}
    assert len(client_init_kwargs) == 1
    # Verify client_kwargs() was passed: verify is an SSLContext (never False)
    assert client_init_kwargs[0].get("verify") is not False
    assert "mounts" in client_init_kwargs[0]


def test_log_classic_eval_and_create_sweep() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        seen.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(201, json={"id": "run_ce", "name": "ce-run", "status": "running"})
        if request.method == "POST" and request.url.path == "/api/v1/runs/run_ce/reports":
            return httpx.Response(201, json={"id": "rep_1", **body})
        if request.method == "PATCH" and request.url.path == "/api/v1/runs/run_ce":
            return httpx.Response(200, json={"id": "run_ce", "status": body.get("status", "finished")})
        if request.method == "POST" and request.url.path == "/api/v1/sweeps":
            return httpx.Response(201, json={"id": "sw_new", "name": body["name"], "config": body["config"]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with pytest.warns(DeprecationWarning, match="create_sweep"):
        sweep = client.mlops.create_sweep(
            "lr-sweep",
            method="bayes",
            metric_name="eval/loss",
            metric_goal="minimize",
            search_space={"lr": {"min": 1e-5, "max": 1e-3}},
            early_stopping={"method": "asha", "min_t": 2},
            description="test sweep",
        )
    assert sweep["id"] == "sw_new"
    assert sweep["method"] == "tpe"
    sweep_req = next(b for m, p, b in seen if m == "POST" and p == "/api/v1/sweeps")
    assert sweep_req["config"]["method"] == "tpe"
    assert sweep_req["config"]["early_stop"] == {"type": "asha", "min_iter": 2}

    with (
        pytest.warns(DeprecationWarning, match="create_sweep"),
        pytest.raises(ValueError, match="finite"),
    ):
        client.mlops.create_sweep("bad-sweep", search_space={"lr": float("nan")})

    with pytest.warns(DeprecationWarning, match="start_run"):
        run = client.mlops.start_run("ce-run", flush_interval=60.0)
    with run:
        eval_payload = {"roc_auc": 0.94, "confusion_matrix": [[10, 1], [2, 12]]}
        updated = run.log_classic_eval(eval_payload)
        assert updated["classic_eval"] == eval_payload

        with pytest.raises(ValueError, match="finite"):
            run.log_classic_eval({"roc_auc": float("inf")})

    report_reqs = [b for m, p, b in seen if m == "POST" and p == "/api/v1/runs/run_ce/reports"]
    assert len(report_reqs) == 1
    assert report_reqs[0]["kind"] == "custom"
    assert report_reqs[0]["name"] == "classic_eval"


def test_download_model_rejects_malformed_sha256_before_download(tmp_path: Path) -> None:
    download_attempted = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v1/mlops/models/mod_badsha":
            return httpx.Response(
                200,
                json={
                    "id": "mod_badsha",
                    "name": "badsha-model",
                    "versions": [{"id": "mv_1", "version": 1, "artifact_id": "art_badsha"}],
                    "aliases": {"production": 1},
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/models/mod_badsha/versions/1/files":
            return httpx.Response(
                200,
                json={
                    "model_id": "mod_badsha",
                    "version": 1,
                    "files": [
                        {
                            "artifact_id": "blob_bad",
                            "path": "model.bin",
                            "url": "/api/v1/artifacts/blob_bad/download",
                            "sha256": "not-a-valid-sha256",
                        }
                    ],
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/artifacts/blob_bad/download":
            download_attempted["called"] = True
            return httpx.Response(200, content=b"data")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with (
        pytest.warns(DeprecationWarning, match="download_model"),
        pytest.raises(MatrixError, match="Invalid expected SHA-256"),
    ):
        client.mlops.download_model("mod_badsha", alias="production", dest_dir=tmp_path)
    assert download_attempted["called"] is False


def test_training_run_close_and_post_finish_log_guard(caplog: pytest.LogCaptureFixture) -> None:
    patch_calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(201, json={"id": "run_cls", "name": "close-run", "status": "running"})
        if request.method == "PATCH" and request.url.path == "/api/v1/runs/run_cls":
            patch_calls.append(body)
            return httpx.Response(200, json={"id": "run_cls", "status": body["status"]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with pytest.warns(DeprecationWarning, match="start_run"):
        run = client.mlops.start_run("close-run", flush_interval=60.0)
    run.close()
    assert run.status == "completed"
    assert len(patch_calls) == 1
    assert patch_calls[0]["status"] == "finished"

    # Second close / finish is idempotent
    run.close()
    assert len(patch_calls) == 1

    # Logging after finish logs warning and does not enqueue
    with caplog.at_level("WARNING", logger="matrix_sdk.mlops"):
        run.log({"loss": 0.1}, step=1)
    assert any("finished run" in rec.message for rec in caplog.records)
    assert len(run._run._points) == 0
