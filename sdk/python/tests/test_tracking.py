import hashlib
import json
import logging
import subprocess
import sys
import threading
import time
import zipfile
from io import BytesIO

import pytest

import matrix_sdk as mx
from matrix_sdk import client as client_mod
from matrix_sdk import tracking
from matrix_sdk.tracking import capture_env, capture_git, strip_credentials, to_metric_value

RUN = "/api/v1/runs/r1"


# -- start_run / capture


def test_start_run_body(server, client, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "hunter2-do-not-send")
    run = client.start_run(name="exp", group="g", tags=["a", 1], config={"lr": 0.1, "nested": {"x": [1, 2]}},
                           dataset_version="dv1", system_metrics=False)
    run.finish()
    (_, _, headers, body) = server.calls("POST", "/api/v1/runs")[0]
    assert headers["authorization"] == "Bearer mx_k"
    assert body["name"] == "exp" and body["group"] == "g" and body["tags"] == ["a", "1"]
    assert body["params"] == {"lr": 0.1, "nested": {"x": [1, 2]}}
    assert body["dataset_version_id"] == "dv1"
    assert body["git"]["commit"] and len(body["git"]["commit"]) == 40  # tests run inside the repository
    env = body["env"]
    assert env["python"] and env["platform"] and 0 < len(env["packages"]) <= 500
    assert any(p.startswith("httpx==") for p in env["packages"])
    raw = json.dumps(body)
    assert "hunter2-do-not-send" not in raw and "SUPER_SECRET_TOKEN" not in raw
    assert run.status == "finished" and server.calls("PATCH", RUN)[0][3] == {"status": "finished"}


def test_start_run_failure_raises_matrix_error(server, client):
    server.fail_all = True
    with pytest.raises(mx.MatrixError, match="could not create run"):
        client.start_run(system_metrics=False, capture_git=False, capture_env=False)


def test_start_run_uses_init_default_client(server, client, monkeypatch):
    monkeypatch.setattr(client_mod, "_default_client", client)
    run = mx.start_run(system_metrics=False, capture_git=False, capture_env=False)
    assert mx.current_run() is run
    mx.log({"loss": 1.0})
    mx.log_params({"bs": 8})
    mx.finish()
    assert mx.current_run() is None and run.status == "finished"
    assert server.points()[0]["key"] == "loss"
    assert server.params == {"bs": 8}


def test_module_helpers_without_run():
    tracking._current = None
    with pytest.raises(mx.MatrixError, match="start_run"):
        mx.log({"a": 1})
    mx.finish()  # no-op


def test_no_api_key(monkeypatch):
    monkeypatch.setattr(client_mod, "_default_client", None)
    monkeypatch.delenv("MATRIX_API_KEY", raising=False)
    with pytest.raises(mx.MatrixError, match="API key"):
        mx.start_run()


def test_strip_credentials():
    assert strip_credentials("https://user:tok@github.com/o/r.git") == "https://github.com/o/r.git"
    assert strip_credentials("https://ghp_x@gitlab.com:8443/o/r") == "https://gitlab.com:8443/o/r"
    assert strip_credentials("git@github.com:o/r.git") == "git@github.com:o/r.git"
    assert strip_credentials("https://github.com/o/r") == "https://github.com/o/r"
    assert strip_credentials(None) is None


def test_capture_git_outside_repo_and_failures(tmp_path, monkeypatch):
    assert capture_git(cwd=str(tmp_path)) is None

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("git", 2)

    monkeypatch.setattr(subprocess, "run", boom)
    assert capture_git() is None


def test_capture_git_repo(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("remote", "add", "origin", "https://bob:s3cret@example.com/o/r.git")
    (tmp_path / "f").write_text("x")
    git("add", "f")
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")
    info = capture_git(cwd=str(tmp_path))
    assert info["branch"] == "main" and info["dirty"] is False and len(info["commit"]) == 40
    assert info["remote"] == "https://example.com/o/r.git"
    (tmp_path / "f").write_text("y")
    assert capture_git(cwd=str(tmp_path))["dirty"] is True


def test_capture_env_limit():
    assert len(capture_env(max_packages=3)["packages"]) == 3


# -- values


def test_value_conversion():
    np = pytest.importorskip("numpy")
    assert to_metric_value(float("nan")) == "nan"
    assert to_metric_value(float("inf")) == "inf" and to_metric_value(-float("inf")) == "-inf"
    assert to_metric_value(3) == 3.0 and to_metric_value(True) == 1.0
    assert to_metric_value(np.float32(0.5)) == 0.5 and to_metric_value(np.int64(7)) == 7.0
    assert to_metric_value(np.array(2.5)) == 2.5
    assert to_metric_value(np.float64("nan")) == "nan"
    for bad in ("0.1", None, [1.0], {"a": 1}, b"1", 1j, np.array([1.0, 2.0])):
        with pytest.raises(TypeError):
            to_metric_value(bad)


def test_torch_scalars():
    torch = pytest.importorskip("torch")
    assert to_metric_value(torch.tensor(1.5)) == 1.5
    assert to_metric_value(torch.tensor(float("inf"))) == "inf"
    with pytest.raises(TypeError):
        to_metric_value(torch.ones(3))


def test_log_rejects_bad_input_at_call_time(make_run):
    run = make_run()
    with pytest.raises(TypeError, match="'loss'"):
        run.log({"loss": "high"})
    with pytest.raises(ValueError, match="reserved"):
        run.log({"sys/cpu.percent": 1})
    with pytest.raises(ValueError, match="invalid metric key"):
        run.log({"bad\nkey": 1})
    with pytest.raises(ValueError):
        run.log({"a": 1}, step=-1)
    with pytest.raises(TypeError):
        run.log({"a": 1}, step=1.5)
    assert run.step == 0  # nothing was queued


# -- sending


def test_auto_step_batches_and_special_values(server, make_run):
    run = make_run()
    run.log({"loss": 0.5, "acc": float("nan")})
    run.log_metric("loss", float("inf"))
    run.log_metrics({"loss": 0.1}, step=100)  # explicit: counter unchanged
    run.log({"loss": 0.2})
    for i in range(2500):
        run.log({"x": i})
    run.finish()
    pts = server.points()
    assert [(p["key"], p["step"], p["value"]) for p in pts[:5]] == [
        ("loss", 0, 0.5), ("acc", 0, "nan"), ("loss", 1, "inf"), ("loss", 100, 0.1), ("loss", 2, 0.2)]
    assert all(set(p) == {"key", "step", "value", "ts"} and p["ts"].endswith("Z") for p in pts)
    assert len(pts) == 2505 and pts[-1] == {**pts[-1], "key": "x", "step": 2502, "value": 2499.0}
    sizes = [len(b["points"]) for *_, b in server.calls("POST", f"{RUN}/metrics")]
    assert max(sizes) <= 1000 and sum(sizes) == 2505
    assert run.dropped_points == 0


def test_points_flushed_within_interval(server, make_run):
    run = make_run(flush_interval=0.05)
    run.log({"a": 1})
    deadline = time.monotonic() + 2
    while not server.points() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.points()[0]["value"] == 1.0 and run.status == "running"


def test_threadsafe_log(server, make_run):
    run = make_run()

    def worker():
        for _ in range(500):
            run.log({"v": 1})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    run.finish()
    steps = sorted(p["step"] for p in server.points())
    assert steps == list(range(4000))


def test_bounded_queue_drops_oldest(server, make_run):
    run = make_run(max_queue=10, batch_size=10**9, flush_interval=1000)  # sender idle until finish
    for i in range(25):
        run.log({"x": i})
    assert run.dropped_points == 15
    run.finish()
    assert [p["value"] for p in server.points()] == [float(i) for i in range(15, 25)]


def test_retry_with_backoff(server, make_run):
    server.metrics_failures = 2
    run = make_run()
    run.log({"a": 1})
    run.finish()
    assert len(server.calls("POST", f"{RUN}/metrics")) == 3
    assert [p["value"] for p in server.points()] == [1.0, 1.0, 1.0]  # same batch retried
    assert run.dropped_points == 0


def test_network_down_never_raises_or_blocks(server, make_run, caplog):
    run = make_run(flush_timeout=0.5)
    server.fail_all = True
    start = time.monotonic()
    for i in range(5000):
        run.log({"loss": i})
    run.log_params({"a": 1})
    run.log_report("custom", "r", {"x": 1})
    assert time.monotonic() - start < 2.0  # logging is enqueue-only
    run.finish()
    assert time.monotonic() - start < 5.0
    assert run.status == "finished" and run.dropped_points >= 1


def test_params_coalesced_and_conflict_warns(server, make_run, caplog):
    run = make_run()
    with run._cv:  # sender cannot run in between: both calls merge into one POST
        run.log_params({"lr": 0.1})
        run.log_params({"bs": 32, "opt": {"name": "adam"}})
    run.flush()
    assert [b for *_, b in server.calls("POST", f"{RUN}/params")] == [
        {"params": {"lr": 0.1, "bs": 32, "opt": {"name": "adam"}}}]
    with caplog.at_level(logging.WARNING, logger="matrix_sdk.tracking"):
        run.log_params({"lr": 0.2})
        run.flush()
    assert "lr" in caplog.text and "already set" in caplog.text
    run.log({"after": 1})  # keeps going
    run.finish()
    assert server.points()[-1]["key"] == "after"


def test_context_manager_failed_and_killed(server, client):
    with pytest.raises(ValueError), client.start_run(system_metrics=False, capture_git=False,
                                                     capture_env=False) as run:
        secret = "local-variable-value"  # noqa: F841 - must not leak into the error string
        raise ValueError("boom")
    patch = server.calls("PATCH", RUN)[-1][3]
    assert patch == {"status": "failed", "error": "ValueError: boom"} and run.status == "failed"
    with pytest.raises(KeyboardInterrupt), client.start_run(system_metrics=False, capture_git=False,
                                                            capture_env=False):
        raise KeyboardInterrupt
    assert server.calls("PATCH", RUN)[-1][3]["status"] == "killed"


def test_finish_idempotent_and_validates(server, make_run):
    run = make_run()
    with pytest.raises(ValueError):
        run.finish("running")
    run.finish("killed")
    run.finish()
    assert len(server.calls("PATCH", RUN)) == 1
    run.log({"late": 1})  # dropped, no error
    assert run.dropped_points == 1


def test_atexit_handler(server, make_run, monkeypatch):
    run = make_run()
    monkeypatch.setattr(sys, "last_exc", RuntimeError("crash"), raising=False)
    run._atexit()
    assert server.calls("PATCH", RUN)[-1][3] == {"status": "failed", "error": "RuntimeError: crash"}
    run2 = make_run()
    monkeypatch.delattr(sys, "last_exc")
    monkeypatch.delattr(sys, "last_value", raising=False)
    run2._atexit()
    assert server.calls("PATCH", RUN)[-1][3] == {"status": "finished"}


def test_heartbeat(server, make_run):
    make_run(heartbeat_interval=0.02)
    deadline = time.monotonic() + 2
    while not server.calls("POST", f"{RUN}/heartbeat") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.calls("POST", f"{RUN}/heartbeat")


def test_system_metrics_flow_into_run(server, make_run):
    class Fake:
        def sample(self):
            return {"sys/cpu.percent": 12.5}

    run = make_run(system_metrics=True, system_interval=0.02, system_backends=[Fake()])
    time.sleep(0.15)
    run.finish()
    sys_pts = [p for p in server.points() if p["key"] == "sys/cpu.percent"]
    assert sys_pts and sys_pts[0]["value"] == 12.5 and sys_pts[0]["step"] == 0
    assert run.step == 0  # telemetry does not consume the run's step counter


# -- artifacts and reports


def test_log_artifact_file(server, make_run, tmp_path):
    f = tmp_path / "model.bin"
    data = b"\x00weights" * 300_000  # > 1 chunk
    f.write_bytes(data)
    run = make_run()
    art = run.log_artifact(f, name="best", kind="model")
    assert art["id"] == "a1"
    (_, _, headers, body) = server.calls("POST", "/api/v1/artifacts")[0]
    assert headers["x-filename"] == "model.bin" and headers["x-sha256"] == hashlib.sha256(data).hexdigest()
    assert headers["x-owner-kind"] == "run" and headers["x-owner-id"] == "r1"
    assert body == data
    assert server.calls("POST", f"{RUN}/artifacts")[0][3] == {"artifact_id": "a1", "name": "best", "kind": "model"}


def test_log_artifact_directory_zipped(server, make_run, tmp_path):
    d = tmp_path / "ckpt"
    d.mkdir()
    (d / "config.json").write_text("{}")
    run = make_run()
    run.log_artifact(d)
    (_, _, headers, body) = server.calls("POST", "/api/v1/artifacts")[0]
    assert headers["x-filename"] == "ckpt.zip" and headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(BytesIO(body)).namelist() == ["config.json"]
    assert server.calls("POST", f"{RUN}/artifacts")[0][3]["name"] == "ckpt.zip"


def test_log_artifact_errors(server, make_run, tmp_path):
    run = make_run()
    with pytest.raises(FileNotFoundError):
        run.log_artifact(tmp_path / "missing")
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(ValueError):
        run.log_artifact(f, kind="weights")
    server.fail_all = True
    assert run.log_artifact(f, retries=2) is None  # network failure: logged, not raised


def test_log_table_truncation(server, make_run):
    run = make_run()
    rows = [[i, "t" * 3000 if i == 0 else "ok", float("nan")] for i in range(1500)]
    run.log_table("preds", ["i", "text", "score"], rows)
    run.finish()
    body = server.calls("POST", f"{RUN}/reports")[0][3]
    assert body["kind"] == "table" and body["name"] == "preds"
    data = body["data"]
    assert data["columns"] == ["i", "text", "score"] and data["truncated"] is True
    assert len(data["rows"]) == 1000 and len(data["rows"][0][1]) == 2000 and data["rows"][0][2] == "nan"


def test_log_table_small_not_truncated(server, make_run):
    run = make_run()
    run.log_table("t", ["a"], [[1], [2]])
    run.finish()
    assert server.calls("POST", f"{RUN}/reports")[0][3]["data"] == {"columns": ["a"], "rows": [[1], [2]],
                                                                    "truncated": False}


def test_log_report_validation(make_run):
    run = make_run()
    with pytest.raises(ValueError, match="kind"):
        run.log_report("roc", "x", {})
    with pytest.raises(ValueError, match="limit"):
        run.log_report("custom", "big", {"blob": "x" * (3 * 1024 * 1024)})
