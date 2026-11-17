"""Tests for matrix_sdk.mlops (TrainingRun, MLOpsClient, MatrixTrainerCallback)."""

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
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs":
            return httpx.Response(201, json={"id": "run_1", "name": "lora-sft", "status": "running"})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs/run_1/metrics":
            return httpx.Response(202, json={"accepted": len(body["points"]), "should_stop": False, "stop_reason": None})
        if request.method == "PATCH" and request.url.path == "/api/v1/mlops/runs/run_1":
            return httpx.Response(200, json={"id": "run_1", "status": body["status"]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    assert isinstance(client.mlops, MLOpsClient)

    with client.start_run("lora-sft", run_type="lora", hyperparameters={"lr": 2e-4}, flush_interval=60.0) as run:
        assert isinstance(run, TrainingRun)
        run.log({"loss": 1.25, "lr": 2e-4}, step=1, epoch=0.1)
        run.log({"loss": 0.95}, step=2, epoch=0.2)

    assert calls[0][0:2] == ("POST", "/api/v1/mlops/runs")
    assert calls[0][2]["hyperparams"] == {"lr": 2e-4}
    metric_calls = [c for c in calls if c[1] == "/api/v1/mlops/runs/run_1/metrics"]
    assert len(metric_calls) >= 1
    all_points = [pt for c in metric_calls for pt in c[2]["points"]]
    assert len(all_points) == 3
    assert {p["metric_name"] for p in all_points} == {"loss", "lr"}
    assert calls[-1][0:2] == ("PATCH", "/api/v1/mlops/runs/run_1")
    assert calls[-1][2]["status"] == "completed"


def test_training_run_marks_failed_on_exception_and_rejects_non_finite() -> None:
    patch_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs":
            return httpx.Response(201, json={"id": "run_fail", "name": "fail-test", "status": "running"})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs/run_fail/metrics":
            return httpx.Response(202, json={"accepted": len(body["points"]), "should_stop": False, "stop_reason": None})
        if request.method == "PATCH" and request.url.path == "/api/v1/mlops/runs/run_fail":
            patch_bodies.append(body)
            return httpx.Response(200, json={"id": "run_fail", "status": body["status"]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)

    with (
        pytest.raises(RuntimeError, match="oom"),
        client.start_run("fail-test", flush_interval=60.0) as run,
    ):
        with pytest.raises(ValueError, match="finite"):
            run.log({"loss": float("nan")}, step=1)
        with pytest.raises(ValueError, match="finite"):
            run.log({"loss": float("inf")}, step=1)
        run.log({"loss": 2.0}, step=1)
        raise RuntimeError("oom")

    assert len(patch_bodies) == 1
    assert patch_bodies[0]["status"] == "failed"
    assert "RuntimeError: oom" in patch_bodies[0]["error"]


def test_log_artifact_upload_and_deduplication(tmp_path: Path) -> None:
    adapter_file = tmp_path / "adapter_model.safetensors"
    payload = b"safetensors-binary-payload-" * 1024
    adapter_file.write_bytes(payload)
    expected_sha = hashlib.sha256(payload).hexdigest()

    uploaded_blobs: dict[str, bytes] = {}
    presign_calls: list[dict[str, Any]] = []
    commit_calls: list[str] = []
    call_count = {"presign": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs":
            return httpx.Response(201, json={"id": "run_art", "name": "art-run", "status": "running"})
        if request.method == "PATCH" and request.url.path == "/api/v1/mlops/runs/run_art":
            return httpx.Response(200, json={"id": "run_art", "status": "completed"})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/artifacts/presign-upload":
            body = json.loads(request.content.decode())
            presign_calls.append(body)
            call_count["presign"] += 1
            if call_count["presign"] == 1:
                return httpx.Response(
                    201,
                    json={
                        "id": "art_1",
                        "name": body["name"],
                        "kind": body["kind"],
                        "sha256": body["sha256"],
                        "size_bytes": body["size_bytes"],
                        "status": "pending_upload",
                        "deduplicated": False,
                        "upload_url": "/api/v1/mlops/artifacts/local/tok123",
                    },
                )
            return httpx.Response(
                201,
                json={
                    "id": "art_2",
                    "name": body["name"],
                    "kind": body["kind"],
                    "sha256": body["sha256"],
                    "size_bytes": body["size_bytes"],
                    "status": "committed",
                    "deduplicated": True,
                    "upload_url": None,
                },
            )
        if request.method == "PUT" and request.url.path == "/api/v1/mlops/artifacts/local/tok123":
            uploaded_blobs["tok123"] = request.read()
            return httpx.Response(200, json={"status": "uploaded", "size_bytes": len(uploaded_blobs["tok123"])})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/artifacts/art_1/commit":
            commit_calls.append("art_1")
            return httpx.Response(
                200,
                json={
                    "id": "art_1",
                    "sha256": expected_sha,
                    "size_bytes": len(payload),
                    "status": "committed",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with client.start_run("art-run", flush_interval=60.0) as run:
        art1 = run.log_artifact(adapter_file, name="lora-v1", kind="lora_adapter", format="safetensors")
        assert art1["id"] == "art_1"
        assert art1["status"] == "committed"
        assert uploaded_blobs["tok123"] == payload
        assert commit_calls == ["art_1"]

        art2 = run.log_artifact(adapter_file, name="lora-v1-dup", kind="lora_adapter")
        assert art2["id"] == "art_2"
        assert art2["status"] == "committed"
        assert commit_calls == ["art_1"]  # no extra commit on deduplicated

    assert presign_calls[0]["sha256"] == expected_sha
    assert presign_calls[0]["size_bytes"] == len(payload)
    assert presign_calls[0]["metadata"]["format"] == "safetensors"

    # Rejects directory and symlink
    symlink_path = tmp_path / "link.safetensors"
    symlink_path.symlink_to(adapter_file)
    with pytest.raises(ValueError, match="symlink"):
        run.log_artifact(symlink_path, name="bad")
    with pytest.raises(ValueError, match="regular file"):
        run.log_artifact(tmp_path, name="bad-dir")


def test_run_sweep_agent_runs_trials_and_stops_on_409() -> None:
    trial_counter = {"n": 0}
    seen_hyperparams: list[dict[str, Any]] = []
    finished_runs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/mlops/sweeps/sw_1/trials/next":
            trial_counter["n"] += 1
            if trial_counter["n"] <= 2:
                hp = {"lr": 1e-4 * trial_counter["n"]}
                return httpx.Response(
                    201,
                    json={
                        "sweep_id": "sw_1",
                        "trial_index": trial_counter["n"] - 1,
                        "hyperparams": hp,
                        "run": {"id": f"run_t{trial_counter['n']}", "name": f"trial-{trial_counter['n']}", "status": "running"},
                    },
                )
            return httpx.Response(409, json={"detail": "sweep exhausted"})
        if request.method == "POST" and request.url.path.endswith("/metrics"):
            body = json.loads(request.content.decode())
            return httpx.Response(202, json={"accepted": len(body["points"]), "should_stop": False, "stop_reason": None})
        if request.method == "PATCH" and request.url.path.startswith("/api/v1/mlops/runs/"):
            finished_runs.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"status": "completed"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)

    def train_fn(run: TrainingRun, hp: dict[str, Any]) -> None:
        seen_hyperparams.append(hp)
        run.log({"eval/loss": 0.5 / (hp["lr"] * 1e4)}, step=1)

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
        if request.method == "POST" and request.url.path == "/api/v1/mlops/models/mod_1/aliases":
            return httpx.Response(200, json={"model_id": "mod_1", "alias": body["alias"], "version": body["version"]})
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
        if request.method == "GET" and request.url.path == "/api/v1/mlops/artifacts/art_dl/download-url":
            return httpx.Response(
                200,
                json={
                    "artifact_id": "art_dl",
                    "download_url": "/api/v1/mlops/artifacts/local/dl_tok",
                    "storage_backend": "local",
                    "sha256": bad_sha if serve_bad_sha["bad"] else good_sha,
                    "size_bytes": len(model_bytes),
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/mlops/artifacts/local/dl_tok":
            return httpx.Response(200, content=model_bytes)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    model = client.register_model("llama-lora", task_type="lora", base_model="meta-llama/Llama-3-8B")
    assert model["id"] == "mod_1"

    mv = client.mlops.create_model_version("mod_1", artifact_id="art_dl", run_id="run_1", lora_config={"r": 16})
    assert mv["version"] == 1

    alias_resp = client.mlops.set_model_alias("mod_1", "production", 1)
    assert alias_resp["alias"] == "production"

    downloaded = client.mlops.download_model("mod_1", alias="production", dest_dir=tmp_path)
    assert downloaded.exists()
    assert downloaded.read_bytes() == model_bytes

    serve_bad_sha["bad"] = True
    with pytest.raises(MatrixError, match="SHA-256 mismatch"):
        client.mlops.download_model("mod_1", alias="production", dest_dir=tmp_path / "corrupt")


def test_matrix_trainer_callback_logs_and_propagates_early_stop() -> None:
    metric_batches: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs":
            return httpx.Response(201, json={"id": "run_cb", "name": "cb-run", "status": "running"})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs/run_cb/metrics":
            metric_batches.append(body)
            return httpx.Response(202, json={"accepted": len(body["points"]), "should_stop": True, "stop_reason": "ASHA pruned"})
        if request.method == "PATCH" and request.url.path == "/api/v1/mlops/runs/run_cb":
            return httpx.Response(200, json={"id": "run_cb", "status": "completed"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with client.start_run("cb-run", flush_interval=60.0) as run:
        cb = MatrixTrainerCallback(run)
        state = SimpleNamespace(global_step=10, epoch=1.0)
        control = SimpleNamespace(should_training_stop=False)
        args = SimpleNamespace()

        cb.on_log(args, state, control, logs={"loss": 0.42, "eval_loss": 0.51, "note": "ignored"})
        run.flush()
        assert run.should_stop is True
        assert run.stop_reason == "ASHA pruned"

        cb.on_step_end(args, state, control)
        assert control.should_training_stop is True

        cb.on_evaluate(args, state, control, metrics={"eval_accuracy": 0.91})
        cb.on_train_end(args, state, control)

    points = [p for b in metric_batches for p in b["points"]]
    names = {p["metric_name"]: p["category"] for p in points}
    assert names["loss"] == "train"
    assert names["eval_loss"] == "eval"
    assert names["eval_accuracy"] == "eval"


def test_protocol_relative_url_rejected_without_forwarding_auth(tmp_path: Path) -> None:
    adapter_file = tmp_path / "weights.safetensors"
    payload = b"weights-data"
    adapter_file.write_bytes(payload)
    good_sha = hashlib.sha256(payload).hexdigest()
    evil_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "evil.example":
            evil_requests.append(request)
            return httpx.Response(200)
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs":
            return httpx.Response(201, json={"id": "run_sec", "name": "sec-run", "status": "running"})
        if request.method == "PATCH" and request.url.path == "/api/v1/mlops/runs/run_sec":
            return httpx.Response(200, json={"id": "run_sec", "status": "completed"})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/artifacts/presign-upload":
            return httpx.Response(
                201,
                json={
                    "id": "art_evil",
                    "deduplicated": False,
                    "upload_url": "//evil.example/leak-upload",
                },
            )
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
        if request.method == "GET" and request.url.path == "/api/v1/mlops/artifacts/art_evil/download-url":
            return httpx.Response(
                200,
                json={
                    "artifact_id": "art_evil",
                    "download_url": "//evil.example/leak-download",
                    "sha256": good_sha,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with (
        client.start_run("sec-run", flush_interval=60.0) as run,
        pytest.raises(ValueError, match="Unsupported external URL scheme"),
    ):
        run.log_artifact(adapter_file, name="evil-art")

    with pytest.raises(ValueError, match="Unsupported external URL scheme"):
        client.mlops.download_model("mod_sec", alias="production", dest_dir=tmp_path)

    assert evil_requests == []


def test_external_presigned_put_and_get_omit_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_file = tmp_path / "model.bin"
    payload = b"presigned-s3-payload-" * 16
    adapter_file.write_bytes(payload)
    expected_sha = hashlib.sha256(payload).hexdigest()

    ext_requests: list[tuple[str, str, dict[str, str], bytes]] = []
    real_httpx_client = httpx.Client

    def ext_handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        ext_requests.append((request.method, str(request.url), dict(request.headers), body))
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, content=payload)
        raise AssertionError(f"Unexpected external request: {request.method} {request.url}")

    def patched_client(*args: Any, **kwargs: Any) -> httpx.Client:
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.MockTransport(ext_handler)
        return real_httpx_client(*args, **kwargs)

    monkeypatch.setattr("matrix_sdk.mlops.httpx.Client", patched_client)

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer test-key"
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs":
            return httpx.Response(201, json={"id": "run_s3", "name": "s3-run", "status": "running"})
        if request.method == "PATCH" and request.url.path == "/api/v1/mlops/runs/run_s3":
            return httpx.Response(200, json={"id": "run_s3", "status": "completed"})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/artifacts/presign-upload":
            return httpx.Response(
                201,
                json={
                    "id": "art_s3",
                    "deduplicated": False,
                    "upload_url": "https://s3.example.com/bucket/art_s3?X-Amz-Signature=abc",
                },
            )
        if request.method == "POST" and request.url.path == "/api/v1/mlops/artifacts/art_s3/commit":
            return httpx.Response(200, json={"id": "art_s3", "status": "committed"})
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
        if request.method == "GET" and request.url.path == "/api/v1/mlops/artifacts/art_s3/download-url":
            return httpx.Response(
                200,
                json={
                    "artifact_id": "art_s3",
                    "download_url": "https://s3.example.com/bucket/art_s3?X-Amz-Signature=def",
                    "sha256": expected_sha,
                },
            )
        raise AssertionError(f"Unexpected API request: {request.method} {request.url}")

    client = _make_client(api_handler)
    with client.start_run("s3-run", flush_interval=60.0) as run:
        committed = run.log_artifact(adapter_file, name="s3-art")
        assert committed["status"] == "committed"

    dl_path = client.mlops.download_model("mod_s3", alias="production", dest_dir=tmp_path / "dl")
    assert dl_path.read_bytes() == payload

    assert len(ext_requests) == 2
    put_req, get_req = ext_requests
    assert put_req[0] == "PUT"
    assert put_req[3] == payload
    assert "authorization" not in {k.lower() for k in put_req[2]}
    assert get_req[0] == "GET"
    assert "authorization" not in {k.lower() for k in get_req[2]}


def test_log_classic_eval_and_create_sweep() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        seen.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs":
            return httpx.Response(201, json={"id": "run_ce", "name": "ce-run", "status": "running"})
        if request.method == "PATCH" and request.url.path == "/api/v1/mlops/runs/run_ce":
            return httpx.Response(200, json={"id": "run_ce", "status": body.get("status", "running"), **body})
        if request.method == "POST" and request.url.path == "/api/v1/mlops/sweeps":
            return httpx.Response(201, json={"id": "sw_new", "name": body["name"], "method": body["method"]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
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

    with pytest.raises(ValueError, match="finite"):
        client.mlops.create_sweep("bad-sweep", search_space={"lr": float("nan")})

    with client.start_run("ce-run", flush_interval=60.0) as run:
        eval_payload = {"roc_auc": 0.94, "confusion_matrix": [[10, 1], [2, 12]]}
        updated = run.log_classic_eval(eval_payload)
        assert updated["classic_eval"] == eval_payload

        with pytest.raises(ValueError, match="finite"):
            run.log_classic_eval({"roc_auc": float("inf")})


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
        if request.method == "GET" and request.url.path == "/api/v1/mlops/artifacts/art_badsha/download-url":
            return httpx.Response(
                200,
                json={
                    "artifact_id": "art_badsha",
                    "download_url": "/api/v1/mlops/artifacts/local/tok",
                    "sha256": "not-a-valid-sha256",
                },
            )
        if request.method == "GET" and request.url.path == "/api/v1/mlops/artifacts/local/tok":
            download_attempted["called"] = True
            return httpx.Response(200, content=b"data")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    with pytest.raises(MatrixError, match="Invalid expected SHA-256"):
        client.mlops.download_model("mod_badsha", alias="production", dest_dir=tmp_path)
    assert download_attempted["called"] is False


def test_training_run_close_and_post_finish_log_guard(caplog: pytest.LogCaptureFixture) -> None:
    patch_calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        if request.method == "POST" and request.url.path == "/api/v1/mlops/runs":
            return httpx.Response(201, json={"id": "run_cls", "name": "close-run", "status": "running"})
        if request.method == "PATCH" and request.url.path == "/api/v1/mlops/runs/run_cls":
            patch_calls.append(body)
            return httpx.Response(200, json={"id": "run_cls", "status": body["status"]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = _make_client(handler)
    run = client.start_run("close-run", flush_interval=60.0)
    run.close()
    assert run.status == "completed"
    assert len(patch_calls) == 1

    # Second close / finish is idempotent
    run.close()
    assert len(patch_calls) == 1

    # Logging after finish logs warning and does not enqueue
    with caplog.at_level("WARNING", logger="matrix_sdk.mlops"):
        run.log({"loss": 0.1}, step=1)
    assert any("finished run" in rec.message for rec in caplog.records)
    assert run._queue.empty()
